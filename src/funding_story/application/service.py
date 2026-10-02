import hashlib
import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from pydantic import ValidationError

from ..config import settings
from ..domain.repositories import Record, RecordRepository
from ..input_image_errors import InputImageValidationError
from ..job_lease import leased_record
from ..models import (
    ConfirmRequest,
    FundingStoryContext,
    MessageRequest,
    Review,
    RunRequest,
    SessionCreateRequest,
    SourceImageRef,
)
from ..worker_errors import JobOwnershipLost


class ApplicationConflict(Exception):
    pass


class ApplicationInvalid(Exception):
    pass


def _image_identity(image: dict) -> dict:
    # A reissued signature identifies the same object. Host/path changes do not.
    reference = SourceImageRef.model_validate({key: value for key, value in image.items() if key != "file_url"})
    identity = reference.model_dump(mode="json", exclude={"read_url", "expires_at"})
    identity["object_url"] = str(reference.read_url).split("?", 1)[0].split("#", 1)[0]
    return identity


def _request_fingerprint(body: MessageRequest | RunRequest) -> str:
    data = body.model_dump(mode="json")
    if isinstance(body, MessageRequest):
        # Core context and its expiring read URLs are refreshed by BE independently
        # of the seller's message. Only the message and attached objects define it.
        data.pop("context")
        data["attachments"] = [
            {**_image_identity(image), "file_url": image["file_url"]} for image in data["attachments"]
        ]
    else:
        data["context"]["source_images"] = [
            _image_identity(image) for image in data["context"]["source_images"]
        ]
    canonical = json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _legacy_run_fingerprint(body: RunRequest) -> str:
    canonical = json.dumps(
        body.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _public_attachment(image: dict) -> dict:
    return {key: image[key] for key in ("slot_id", "file_url", "reward_id", "content_type", "file_size")}


def _refreshed_context(data: dict, context: FundingStoryContext) -> FundingStoryContext:
    """Require every accepted chat attachment when BE refreshes the Core context."""
    slots = {
        attachment["slot_id"]
        for message in data.get("messages", [])
        for attachment in message.get("attachments", [])
    }
    previous = {image["slot_id"]: image for image in data["context"]["source_images"]}
    current = {image.slot_id: image.model_dump(mode="json") for image in context.source_images}
    for slot in slots:
        if slot not in current:
            raise ApplicationInvalid("기존 채팅 첨부 이미지를 포함해 읽기 URL을 다시 발급해 주세요.")
        if _image_identity(previous[slot]) != _image_identity(current[slot]):
            raise ApplicationConflict("기존 첨부 이미지의 파일 또는 메타데이터가 변경되었습니다.")
        if SourceImageRef.model_validate(current[slot]).expires_at <= datetime.now(UTC):
            raise ApplicationInvalid("첨부 이미지 읽기 URL을 다시 발급해 주세요.")
    # New chat attachments must first be accepted as part of a message.
    if any(slot.startswith("chat.") and slot not in slots for slot in current):
        raise ApplicationInvalid("새 첨부 이미지는 채팅 메시지로 먼저 전달해 주세요.")
    return context


class FundingStoryApplication:
    """TTL-only Funding Story state. Final results are delivered to and owned by BE."""

    def __init__(self, records: RecordRepository):
        self._records = records

    def readiness(self) -> tuple[bool, str]:
        return self._records.ready()

    @staticmethod
    def _session_public(row: Record) -> dict:
        data = row["data"]
        review = Review.model_validate(data["review"]) if data.get("review") else None
        summary = None
        if review is not None and not review.missing:
            summary = {
                "product": review.product,
                "story": review.story,
                "strengths": [
                    {"title": strength.title, "description": strength.description}
                    for strength in review.strengths
                ],
            }
        return {
            "session_id": row["id"],
            "revision": row["revision"],
            "confirmed_revision": data.get("confirmed_revision"),
            "messages": [
                {
                    "role": message["role"], "text": message["text"],
                    **({"attachments": message["attachments"]} if message.get("attachments") else {}),
                }
                for message in data.get("messages", [])
            ],
            "missing": review.missing if review is not None else [],
            "summary": summary,
            "active_chat_id": data.get("active_chat_id"),
        }

    @staticmethod
    def _chat_accepted(row: Record) -> dict:
        return {"chat_id": row["id"], "status": "queued"}

    def create_session(self, project: str, body: SessionCreateRequest) -> dict:
        session_id = self._records.create(
            project,
            "session",
            {
                "context": body.context.model_dump(mode="json"),
                "messages": [],
                "message_requests": {},
                "review": None,
                "confirmed_revision": None,
                "active_chat_id": None,
                "active_run_id": None,
            },
        )
        return self._session_public(self._records.get(session_id, project, kind="session"))

    def latest_session(self, project: str) -> dict | None:
        row = self._records.latest(project, "session")
        return self._session_public(row) if row else None

    def get_session(self, session_id: str, project: str) -> dict:
        return self._session_public(self._records.get(session_id, project, kind="session"))

    def start_session(self, session_id: str, project: str) -> tuple[dict, bool]:
        with self._records.transaction() as tx:
            session = tx.get(session_id, project, lock=True, kind="session")
            data = session["data"]
            if data["messages"]:
                raise ApplicationConflict("이미 시작된 대화입니다.")
            active_id = data.get("active_chat_id")
            if active_id:
                active = tx.get(active_id, project, kind="chat")
                if active["data"]["status"] in ("queued", "running"):
                    return self._chat_accepted(active), False
            if data.get("active_run_id"):
                raise ApplicationConflict("전체 생성 작업을 처리 중입니다.")
            chat_id = tx.create(
                project,
                "chat",
                {
                    "status": "queued",
                    "mode": "initial",
                    "session_id": session_id,
                    "source_revision": session["revision"],
                    "reply": "",
                    "error": None,
                },
            )
            data.update(active_chat_id=chat_id, confirmed_revision=None)
            tx.save(session_id, data)
            chat = tx.get(chat_id, project, kind="chat")
        return self._chat_accepted(chat), True

    def add_message(
        self,
        session_id: str,
        body: MessageRequest,
        project: str,
    ) -> tuple[dict, bool]:
        fingerprint = _request_fingerprint(body)
        with self._records.transaction() as tx:
            session = tx.get(session_id, project, lock=True, kind="session")
            data = session["data"]
            existing = data["message_requests"].get(body.message_id)
            if existing:
                matches = (
                    existing["fingerprint"] == fingerprint
                    if "fingerprint" in existing
                    else not body.attachments
                    and existing["text"] == body.text
                    and existing["revision"] == body.revision
                )
                if not matches:
                    raise ApplicationConflict("동일 요청 ID의 내용이 다릅니다.")
                return self._chat_accepted(tx.get(existing["chat_id"], project, kind="chat")), False
            if session["revision"] != body.revision:
                raise ApplicationConflict("입력 버전이 변경되었습니다.")
            active_id = data.get("active_chat_id")
            if active_id and tx.get(active_id, project, kind="chat")["data"]["status"] in (
                "queued",
                "running",
            ):
                raise ApplicationConflict("이전 답변을 처리 중입니다.")
            if data.get("active_run_id"):
                raise ApplicationConflict("전체 생성 작업을 처리 중입니다.")
            context = (
                _refreshed_context(data, body.context)
                if body.context is not None
                else FundingStoryContext.model_validate(data["context"])
            )
            images = {image.slot_id: image.model_dump(mode="json") for image in context.source_images}
            for image in body.attachments:
                serialized = image.model_dump(mode="json", exclude={"file_url"})
                if image.expires_at <= datetime.now(UTC):
                    raise ApplicationInvalid("첨부 이미지 읽기 URL을 다시 발급해 주세요.")
                if image.slot_id in images and _image_identity(images[image.slot_id]) != _image_identity(serialized):
                    raise ApplicationConflict("동일 첨부 이미지 ID의 파일 또는 메타데이터가 다릅니다.")
                images[image.slot_id] = serialized
            try:
                context = FundingStoryContext.model_validate(
                    {**context.model_dump(mode="json"), "source_images": list(images.values())}
                )
            except ValidationError as exc:
                raise ApplicationInvalid("첨부 이미지 수 또는 리워드 정보가 올바르지 않습니다.") from exc
            if any(image.expires_at <= datetime.now(UTC) for image in context.source_images):
                raise ApplicationInvalid("입력 이미지 읽기 URL을 갱신한 context를 전달해 주세요.")
            chat_id = tx.create(
                project,
                "chat",
                {
                    "status": "queued",
                    "session_id": session_id,
                    "source_revision": session["revision"],
                    "reply": "",
                    "error": None,
                },
            )
            data["context"] = context.model_dump(mode="json")
            message = {"role": "user", "text": body.text}
            if body.attachments:
                message["attachments"] = [
                    _public_attachment(image.model_dump(mode="json")) for image in body.attachments
                ]
            data["messages"].append(message)
            data["message_requests"][body.message_id] = {
                "text": body.text,
                "revision": body.revision,
                "chat_id": chat_id,
                "fingerprint": fingerprint,
            }
            data.update(active_chat_id=chat_id, confirmed_revision=None)
            tx.save(session_id, data)
            chat = tx.get(chat_id, project, kind="chat")
        return self._chat_accepted(chat), True

    def get_chat(self, chat_id: str, project: str) -> dict:
        row = self._records.get(chat_id, project, kind="chat")
        data = row["data"]
        session = self._records.get(data["session_id"], project, kind="session")
        return {
            "chat_id": chat_id,
            "status": data["status"],
            "session_id": data["session_id"],
            "revision": session["revision"],
            "reply": data.get("reply", ""),
            "error": data.get("error"),
        }

    def chat_context(self, chat_id: str, project: str) -> tuple[Record, Record]:
        chat = self._records.get(chat_id, project, kind="chat")
        session = self._records.get(chat["data"]["session_id"], project, kind="session")
        return chat, session

    def confirm_session(self, session_id: str, body: ConfirmRequest, project: str) -> dict:
        with self._records.transaction() as tx:
            row = tx.get(session_id, project, lock=True, kind="session")
            if row["revision"] != body.revision or row["data"].get("active_chat_id"):
                raise ApplicationConflict("최신 답변을 확인해 주세요.")
            if not row["data"].get("review"):
                raise ApplicationInvalid("요약 정리가 필요합니다.")
            review = Review.model_validate(row["data"]["review"])
            if (
                review.missing
                or not review.product
                or not review.story
                or len(review.problems) != 4
                or len(review.strengths) < 3
            ):
                raise ApplicationInvalid("부족한 정보를 먼저 보완해 주세요.")
            row["data"]["confirmed_revision"] = body.revision
            tx.save(session_id, row["data"])
        return {"session_id": session_id, "confirmed_revision": body.revision}

    def create_run(self, body: RunRequest, project: str) -> tuple[dict, bool]:
        fingerprint = _request_fingerprint(body)
        with self._records.transaction() as tx:
            tx.lock_request(project + body.idempotency_key)
            session = tx.get(body.session_id, project, lock=True, kind="session")
            existing = tx.get_request(project, body.idempotency_key)
            if existing:
                if existing["fingerprint"] not in (fingerprint, _legacy_run_fingerprint(body)):
                    raise ApplicationConflict("중복 키의 입력이 다릅니다.")
                row = tx.get(existing["record_id"], project, kind="run")
                return {"run_id": row["id"], "status": "queued"}, False
            if (
                session["revision"] != body.confirmed_revision
                or session["data"].get("confirmed_revision") != body.confirmed_revision
            ):
                raise ApplicationConflict("최신 입력을 확인한 뒤 생성해 주세요.")
            active_run_id = session["data"].get("active_run_id")
            if active_run_id:
                active = tx.get(active_run_id, project, kind="run")
                if active["data"]["status"] in ("queued", "running"):
                    raise ApplicationConflict("전체 생성 작업을 처리 중입니다.")
            # The latest Core context replaces the session's previous context. It is not copied
            # into a durable run snapshot.
            context = _refreshed_context(session["data"], body.context)
            session["data"]["context"] = context.model_dump(mode="json")
            tx.save(body.session_id, session["data"])
            run_id = tx.create(
                project,
                "run",
                {
                    "status": "queued",
                    "session_id": body.session_id,
                    "_queued_at": time.time(),
                    "confirmed_revision": body.confirmed_revision,
                    "error": None,
                },
            )
            session["data"]["active_run_id"] = run_id
            tx.save(body.session_id, session["data"])
            tx.add_request(project, body.idempotency_key, fingerprint, run_id)
        return {"run_id": run_id, "status": "queued"}, True

    def worker_context(self, run_id: str) -> tuple[FundingStoryContext, Review, list[dict]]:
        run = self._records.get(run_id, kind="run")
        session = self._records.get(
            run["data"]["session_id"],
            run["project_id"],
            kind="session",
        )
        if session["revision"] != run["data"]["confirmed_revision"]:
            raise ApplicationConflict("확인된 입력 revision이 변경되었습니다.")
        return (
            FundingStoryContext.model_validate(session["data"]["context"]),
            Review.model_validate(session["data"]["review"]),
            [
                {"role": message["role"], "text": message["text"]}
                for message in session["data"]["messages"]
            ],
        )

    def complete_chat(
        self,
        *,
        session_id: str,
        chat_id: str,
        project: str,
        source_revision: int,
        review: dict,
        reply: str,
    ) -> None:
        with self._records.transaction() as tx:
            current = tx.get(session_id, project, lock=True, kind="session")
            if current["revision"] != source_revision:
                raise ApplicationConflict("대화 처리 중 입력이 변경되었습니다.")
            body = current["data"]
            body["review"] = review
            body["messages"].append({"role": "assistant", "text": reply})
            body["active_chat_id"] = None
            body["confirmed_revision"] = None
            new_revision = current["revision"] + 1
            tx.save(session_id, body, new_revision)
            chat = tx.get(chat_id, project, kind="chat")
            chat["data"].update(status="succeeded", revision=new_revision, error=None)
            tx.save(chat_id, chat["data"])

    @staticmethod
    def _check_owner(row, lease_token):
        if lease_token and (
            row["data"].get("_lease_token") != lease_token
            or float(row["data"].get("_lease_until", 0)) <= time.time()
        ):
            raise JobOwnershipLost("Job lease ownership changed")

    def complete_run_delivery(self, run_id: str, status: str, *, lease_token=None) -> None:
        with self._records.transaction() as tx:
            row = tx.get(run_id, lock=True, kind="run")
            self._check_owner(row, lease_token)
            session = tx.get(row["data"]["session_id"], row["project_id"], lock=True, kind="session")
            failure = row["data"].get("_completion", {}).get("error") if status == "failed" else None
            row["data"].update(status=status, error=failure)
            row["data"].pop("_completion", None)
            row["data"].pop("_retry_after", None)
            tx.save(run_id, row["data"])
            if session["data"].get("active_run_id") == run_id:
                session["data"]["active_run_id"] = None
                tx.save(session["id"], session["data"])

    def get_job(self, record_id: str) -> Record:
        return self._records.get(record_id)

    def job_queued_at(self, row: Record) -> float:
        created = row.get("created_at")
        created = created.timestamp() if hasattr(created, "timestamp") else created
        return float(row["data"].get("_queued_at", created or time.time()))

    def prepare_run_delivery(self, run_id: str, payload: dict, *, lease_token=None) -> dict:
        with self._records.transaction() as tx:
            row = tx.get(run_id, lock=True, kind="run")
            self._check_owner(row, lease_token)
            row["data"].setdefault("_completion", payload)
            row["data"].setdefault("_queued_at", self.job_queued_at(row))
            tx.save(run_id, row["data"])
            return row["data"]["_completion"]

    def retry_run_delivery(self, run_id: str, *, lease_token=None) -> bool:
        with self._records.transaction() as tx:
            row = tx.get(run_id, lock=True, kind="run")
            self._check_owner(row, lease_token)
            if time.time() >= self.job_queued_at(row) + settings().completion_delivery_timeout_seconds:
                return False
            row["data"].update(
                status="queued", _retry_after=time.time() + settings().completion_delivery_retry_seconds,
            )
            tx.save(run_id, row["data"])
            return True

    def pending_job_ids(self) -> list[str]:
        return self._records.pending_job_ids()

    def cleanup_expired(self) -> int:
        cleanup = getattr(self._records, "cleanup_expired", None)
        return cleanup() if cleanup else 0

    @contextmanager
    def claim_job(self, record_id: str) -> Iterator[Record | None]:
        with leased_record(
            self._records,
            record_id,
            queued="queued",
            running="running",
        ) as row:
            yield row

    def fail_job(self, record_id: str, exc: Exception) -> None:
        row = self._records.get(record_id)
        error = {
            "code": "GENERATION_FAILED" if row["kind"] == "run" else "CHAT_FAILED",
            "message": "상세페이지 생성에 실패했습니다."
            if row["kind"] == "run"
            else "답변 생성에 실패했습니다.",
            "retryable": True,
            "detail": None,
        }
        if isinstance(exc, InputImageValidationError):
            error = exc.public_error()
        row["data"].update(status="failed", error=error)
        row["data"].pop("_completion", None)
        row["data"].pop("_retry_after", None)
        self._records.save(record_id, row["data"])
        if row["kind"] == "chat":
            session = self._records.get(
                row["data"]["session_id"],
                row["project_id"],
                kind="session",
            )
            session["data"]["active_chat_id"] = None
            self._records.save(session["id"], session["data"])
        elif row["kind"] == "run":
            session = self._records.get(
                row["data"]["session_id"],
                row["project_id"],
                kind="session",
            )
            if session["data"].get("active_run_id") == record_id:
                session["data"]["active_run_id"] = None
                self._records.save(session["id"], session["data"])
