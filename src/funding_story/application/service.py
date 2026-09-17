import copy
import hashlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from ..domain.repositories import Record, RecordRepository
from ..models import (
    ConfirmRequest,
    ExportCommitRequest,
    ExportRequest,
    MessageRequest,
    ProjectInput,
    Review,
    RunRequest,
    output_information,
)


class ApplicationConflict(Exception):
    pass


class ApplicationInvalid(Exception):
    pass


class ApplicationGone(Exception):
    pass


class ExportRenderingFailed(Exception):
    pass


class FundingStoryApplication:
    """Application use cases backed by a domain repository port."""

    def __init__(self, records: RecordRepository):
        self._records = records

    # Internal worker and asset operations intentionally expose records, never SQL/connections.
    def create(self, project: str, kind: str, data: dict, record_id: str | None = None) -> str:
        return self._records.create(project, kind, data, record_id)

    def get(self, record_id: str, project: str | None = None, *, kind: str | None = None) -> Record:
        return self._records.get(record_id, project, kind=kind)

    def save(self, record_id: str, data: dict, revision: int | None = None) -> None:
        self._records.save(record_id, data, revision)

    def public(self, row: Record) -> dict:
        return self._records.public(row)

    def delete_record(self, record_id: str, project: str, kind: str) -> None:
        self._records.delete_record(record_id, project, kind)

    def readiness(self) -> tuple[bool, str]:
        return self._records.ready()

    def create_session(
        self,
        project: str,
        body: ProjectInput,
        validate_asset: Callable[[str, str], Any],
    ) -> dict:
        for asset_id in body.asset_ids:
            validate_asset(asset_id, project)
        session_id = self._records.create(
            project,
            "session",
            {
                "input": body.model_dump(),
                "messages": [],
                "review": None,
                "confirmed_revision": None,
            },
        )
        return self.public(self._records.get(session_id, project, kind="session"))

    def latest_session(self, project: str) -> dict | None:
        row = self._records.latest(project, "session")
        return self.public(row) if row else None

    def get_session(self, session_id: str, project: str) -> dict:
        return self.public(self._records.get(session_id, project, kind="session"))

    def start_session(self, session_id: str, project: str) -> tuple[dict, bool]:
        with self._records.transaction() as tx:
            row = tx.get(session_id, project, lock=True, kind="session")
            data = row["data"]
            if data["messages"]:
                raise ApplicationConflict("이미 시작된 대화입니다.")
            if data.get("active_chat"):
                active = tx.get(data["active_chat"], project, kind="chat")
                if active["data"]["status"] in ("queued", "running"):
                    return tx.public(active), False
            record_id = tx.create(
                project,
                "chat",
                {
                    "status": "queued",
                    "mode": "initial",
                    "session_id": session_id,
                    "source_revision": row["revision"] + 1,
                    "reply": "",
                },
            )
            data.update(active_chat=record_id, review=None, confirmed_revision=None)
            tx.save(session_id, data, row["revision"] + 1)
            result = tx.public(tx.get(record_id, project, kind="chat"))
        return result, True

    def add_message(
        self,
        session_id: str,
        body: MessageRequest,
        project: str,
        validate_asset: Callable[[str, str], Any],
    ) -> tuple[dict, bool]:
        with self._records.transaction() as tx:
            row = tx.get(session_id, project, lock=True, kind="session")
            data = row["data"]
            for message in data["messages"]:
                if message.get("message_id") == body.message_id:
                    if message["text"] != body.text or message.get("asset_ids", []) != body.asset_ids:
                        raise ApplicationConflict("동일 요청 ID의 내용이 다릅니다.")
                    return tx.public(tx.get(message["job_id"], project)), False
            if row["revision"] != body.revision:
                raise ApplicationConflict("입력 버전이 변경되었습니다.")
            if data.get("active_chat") and tx.get(data["active_chat"], project)["data"]["status"] in (
                "queued",
                "running",
            ):
                raise ApplicationConflict("이전 답변을 처리 중입니다.")
            asset_ids = list(dict.fromkeys(data["input"]["asset_ids"] + body.asset_ids))
            if len(asset_ids) > 30:
                raise ApplicationInvalid("이미지는 최대 30개입니다.")
            for asset_id in body.asset_ids:
                validate_asset(asset_id, project)
            data["input"]["asset_ids"] = asset_ids
            record_id = tx.create(
                project,
                "chat",
                {
                    "status": "queued",
                    "session_id": session_id,
                    "source_revision": row["revision"] + 1,
                    "reply": "",
                },
            )
            data["messages"].append(
                {
                    "role": "user",
                    "text": body.text,
                    "message_id": body.message_id,
                    "job_id": record_id,
                    "asset_ids": body.asset_ids,
                }
            )
            data.update(active_chat=record_id, confirmed_revision=None)
            tx.save(session_id, data, row["revision"] + 1)
            result = tx.public(tx.get(record_id, project))
        return result, True

    def get_chat(self, record_id: str, project: str) -> dict:
        return self.public(self._records.get(record_id, project, kind="chat"))

    def confirm_session(self, session_id: str, body: ConfirmRequest, project: str) -> dict:
        with self._records.transaction() as tx:
            row = tx.get(session_id, project, lock=True, kind="session")
            if row["revision"] != body.revision or row["data"].get("active_chat"):
                raise ApplicationConflict("최신 답변을 확인해 주세요.")
            if not row["data"].get("review"):
                raise ApplicationInvalid("핵심 강점 정리가 필요합니다.")
            review = Review.model_validate(row["data"]["review"])
            if review.missing or len(review.problems) != 4 or len(review.strengths) < 3:
                raise ApplicationInvalid("부족한 정보를 먼저 보완해 주세요.")
            row["data"]["confirmed_revision"] = body.revision
            tx.save(session_id, row["data"])
        return {"confirmed_revision": body.revision}

    def create_run(self, body: RunRequest, project: str) -> tuple[dict, bool]:
        fingerprint = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
        with self._records.transaction() as tx:
            tx.lock_request(project + body.idempotency_key)
            session = tx.get(body.session_id, project, lock=True, kind="session")
            existing = tx.get_request(project, body.idempotency_key)
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise ApplicationConflict("중복 키의 입력이 다릅니다.")
                return tx.public(tx.get(existing["record_id"], project)), False
            if (
                session["revision"] != body.revision
                or session["data"].get("confirmed_revision") != body.revision
            ):
                raise ApplicationConflict("최신 입력을 확인한 뒤 생성해 주세요.")
            record_id = tx.create(
                project,
                "run",
                {
                    "status": "queued",
                    "stage": "접수",
                    "snapshot": session["data"],
                    "source_revision": body.revision,
                    "session_id": body.session_id,
                    "image_jobs": {},
                },
            )
            tx.add_request(project, body.idempotency_key, fingerprint, record_id)
            result = tx.public(tx.get(record_id, project))
        return result, True

    def get_run(self, record_id: str, project: str) -> dict:
        result = self.public(self._records.get(record_id, project, kind="run"))
        result.pop("snapshot", None)
        return result

    def retry_run(self, record_id: str, project: str) -> dict:
        with self._records.transaction() as tx:
            row = tx.get(record_id, project, lock=True, kind="run")
            if row["data"]["status"] not in ("failed", "partially_succeeded"):
                raise ApplicationConflict("재시도 대상이 아닙니다.")
            row["data"].update(status="queued", stage="재시도 대기", error=None)
            if row["data"].get("document") is None:
                row["data"]["retry_cycle"] = row["data"].get("retry_cycle", 0) + 1
            tx.save(record_id, row["data"])
        return {"id": record_id, "status": "queued"}

    @staticmethod
    def _export_public(repository: RecordRepository, row: Record) -> dict:
        result = repository.public(row)
        result.pop("text_overrides", None)
        result.pop("cleanup_asset_ids", None)
        return result

    def export_run(
        self,
        record_id: str,
        body: ExportRequest,
        project: str,
        render: Callable[[dict, str], list[dict]],
        store_asset: Callable[[str, bytes, str], str],
    ) -> dict:
        fingerprint = hashlib.sha256((record_id + body.model_dump_json()).encode()).hexdigest()
        request_key = "export:" + record_id + ":" + body.idempotency_key
        with self._records.transaction() as tx:
            tx.lock_request(project + request_key)
            run = tx.get(record_id, project, kind="run")
            existing = tx.get_request(project, request_key)
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise ApplicationConflict("중복 키의 입력이 다릅니다.")
                return self._export_public(tx, tx.get(existing["record_id"], project, kind="export"))
            data = run["data"]
            if data.get("status") != "succeeded":
                raise ApplicationConflict("모든 이미지가 완료된 생성 결과만 내보낼 수 있습니다.")
            if data.get("source_revision") != body.source_input_revision:
                raise ApplicationConflict("생성 기준 입력 버전이 다릅니다.")
            document = copy.deepcopy(data.get("document"))
            if not document:
                raise ApplicationGone("임시 디자인이 이미 정리되었습니다.")
            text_nodes = {
                node["id"]: node
                for block in document["scene"]["blocks"]
                for node in block["nodes"]
                if node["kind"] == "text"
            }
            unknown = sorted(set(body.text_overrides) - set(text_nodes))
            if unknown:
                raise ApplicationInvalid("정의되지 않은 문구 슬롯: " + ", ".join(unknown[:5]))
            for node_id, value in body.text_overrides.items():
                text_nodes[node_id]["text"] = value
            export_id = tx.create(
                project,
                "export",
                {
                    "status": "rendering",
                    "run_id": record_id,
                    "source_input_revision": body.source_input_revision,
                    "text_overrides": body.text_overrides,
                },
            )
            tx.add_request(project, request_key, fingerprint, export_id)
        try:
            output = []
            for index, image in enumerate(render(document["scene"], project)):
                asset_id = store_asset(project, image["bytes"], "image/png")
                output.append(
                    {
                        "block_id": image["block_id"],
                        "order": index,
                        "asset_id": asset_id,
                        "width": image["width"],
                        "height": image["height"],
                        "alt": image["label"],
                    }
                )
            exported = self._records.get(export_id, project)
            exported["data"].update(
                status="succeeded",
                schema_version=1,
                images=output,
                information=output_information(document.get("information", {})),
                fixed_content={"crowdfunding_notice_key": "fundit.crowdfunding-notice.pending-v1"},
                project_summary={
                    "summary": document.get("summary", ""),
                    "storyline": document.get("storyline", ""),
                },
                committed=False,
            )
            self._records.save(export_id, exported["data"])
        except Exception as exc:
            exported = self._records.get(export_id, project)
            exported["data"].update(status="failed", error=type(exc).__name__)
            self._records.save(export_id, exported["data"])
            raise ExportRenderingFailed("PNG 변환에 실패했습니다.") from exc
        return self.get_export(export_id, project)

    def get_export(self, export_id: str, project: str) -> dict:
        return self._export_public(self._records, self._records.get(export_id, project, kind="export"))

    def commit_export(
        self,
        export_id: str,
        body: ExportCommitRequest,
        project: str,
        delete_asset: Callable[[str, str], None],
    ) -> tuple[dict, str | None]:
        with self._records.transaction() as tx:
            exported = tx.get(export_id, project, lock=True, kind="export")
            data = exported["data"]
            if data.get("status") != "succeeded":
                raise ApplicationConflict("완료된 PNG 결과만 저장 확인할 수 있습니다.")
            if data.get("committed"):
                if data.get("document_revision") != body.document_revision:
                    raise ApplicationConflict("다른 본문 버전으로 이미 저장 확인되었습니다.")
            else:
                run = tx.get(data["run_id"], project, lock=True, kind="run")
                data.update(
                    committed=True,
                    document_revision=body.document_revision,
                    committed_at=datetime.now(UTC).isoformat(),
                    cleanup_pending=True,
                    cleanup_asset_ids=[
                        job["asset_id"]
                        for job in run["data"].get("image_jobs", {}).values()
                        if job.get("status") == "succeeded" and job.get("asset_id")
                    ],
                )
                tx.save(export_id, data)
                run["data"].pop("document", None)
                run["data"].pop("snapshot", None)
                run["data"].pop("image_jobs", None)
                run["data"]["temporary_design_cleared"] = True
                run["data"]["export_id"] = export_id
                tx.save(run["id"], run["data"])
        cleanup_error = None
        try:
            self._records.delete_run_checkpoints(data["run_id"])
            for asset_id in data.get("cleanup_asset_ids", []):
                try:
                    delete_asset(asset_id, project)
                except LookupError:
                    pass
            current = self._records.get(export_id, project)
            current["data"]["cleanup_pending"] = False
            self._records.save(export_id, current["data"])
        except Exception as exc:  # noqa: BLE001 - cleanup is retryable after the durable commit
            cleanup_error = type(exc).__name__
        return self.get_export(export_id, project), cleanup_error

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
        data = self._records.get(record_id)["data"]
        data.update(status="failed", error=type(exc).__name__ + ": " + str(exc)[:500])
        self._records.save(record_id, data)

    def complete_chat(
        self,
        *,
        session_id: str,
        chat_id: str,
        project: str,
        source_revision: int,
        input_data: dict,
        review: dict,
        reply: str,
    ) -> None:
        with self._records.transaction() as tx:
            current = tx.get(session_id, project, lock=True)
            if current["revision"] != source_revision:
                raise ValueError("대화 처리 중 입력이 변경되었습니다.")
            body = current["data"]
            body["input"] = input_data
            body["review"] = review
            body["messages"].append({"role": "assistant", "text": reply})
            body["active_chat"] = None
            tx.save(session_id, body, current["revision"] + 1)
            chat = tx.get(chat_id)["data"]
            chat.update(status="succeeded", review=review, revision=current["revision"] + 1)
            tx.save(chat_id, chat)
