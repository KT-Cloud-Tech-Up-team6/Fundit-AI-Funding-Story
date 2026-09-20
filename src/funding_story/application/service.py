import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager

from ..domain.repositories import Record, RecordRepository
from ..models import (
    ConfirmRequest,
    FundingStoryContext,
    MessageRequest,
    Review,
    RunRequest,
    SessionCreateRequest,
)


class ApplicationConflict(Exception):
    pass


class ApplicationInvalid(Exception):
    pass


def _fingerprint(body: RunRequest) -> str:
    canonical = json.dumps(
        body.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


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
                {"role": message["role"], "text": message["text"]}
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
        with self._records.transaction() as tx:
            session = tx.get(session_id, project, lock=True, kind="session")
            data = session["data"]
            existing = data["message_requests"].get(body.message_id)
            if existing:
                if existing["text"] != body.text or existing["revision"] != body.revision:
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
            data["messages"].append({"role": "user", "text": body.text})
            data["message_requests"][body.message_id] = {
                "text": body.text,
                "revision": body.revision,
                "chat_id": chat_id,
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
        fingerprint = _fingerprint(body)
        with self._records.transaction() as tx:
            tx.lock_request(project + body.idempotency_key)
            session = tx.get(body.session_id, project, lock=True, kind="session")
            existing = tx.get_request(project, body.idempotency_key)
            if existing:
                if existing["fingerprint"] != fingerprint:
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
            session["data"]["context"] = body.context.model_dump(mode="json")
            tx.save(body.session_id, session["data"])
            run_id = tx.create(
                project,
                "run",
                {
                    "status": "queued",
                    "session_id": body.session_id,
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

    def complete_run_delivery(self, run_id: str, status: str) -> None:
        row = self._records.get(run_id, kind="run")
        self._records.save(
            run_id,
            {
                "status": status,
                "confirmed_revision": row["data"]["confirmed_revision"],
                "error": None,
            },
        )
        session = self._records.get(
            row["data"]["session_id"],
            row["project_id"],
            kind="session",
        )
        if session["data"].get("active_run_id") == run_id:
            session["data"]["active_run_id"] = None
            self._records.save(session["id"], session["data"])

    def pending_job_ids(self) -> list[str]:
        return self._records.pending_job_ids()

    @contextmanager
    def claim_job(self, record_id: str) -> Iterator[Record | None]:
        with self._records.transaction() as lock:
            if not lock.try_job_lock(record_id):
                yield None
                return
            try:
                row = self._records.get(record_id)
                if row["data"]["status"] not in ("queued", "running"):
                    yield None
                    return
                row["data"]["status"] = "running"
                self._records.save(record_id, row["data"])
                yield row
            finally:
                lock.unlock_job(record_id)

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
        row["data"].update(status="failed", error=error)
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
