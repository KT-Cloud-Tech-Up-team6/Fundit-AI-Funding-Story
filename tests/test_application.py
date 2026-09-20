import copy
from contextlib import contextmanager
from uuid import uuid4

import pytest

from funding_story.application import ApplicationConflict, FundingStoryApplication
from funding_story.models import ConfirmRequest, RunRequest, SessionCreateRequest


class FakeRecordRepository:
    """In-memory domain-port adapter shared by application service tests."""

    def __init__(self):
        self.records = {}
        self.requests = {}
        self.sequence = 0

    @contextmanager
    def transaction(self):
        yield self

    def create(self, project, kind, data, record_id=None):
        self.sequence += 1
        record_id = record_id or str(uuid4())
        self.records[record_id] = {
            "id": record_id,
            "project_id": project,
            "kind": kind,
            "revision": 1,
            "data": copy.deepcopy(data),
            "sequence": self.sequence,
        }
        return record_id

    def get(self, record_id, project=None, *, lock=False, kind=None):
        row = self.records.get(record_id)
        if not row or (project and row["project_id"] != project) or (kind and row["kind"] != kind):
            raise LookupError(record_id)
        return copy.deepcopy(row)

    def save(self, record_id, data, revision=None):
        self.records[record_id]["data"] = copy.deepcopy(data)
        if revision is not None:
            self.records[record_id]["revision"] = revision

    def latest(self, project, kind):
        matches = [
            row for row in self.records.values() if row["project_id"] == project and row["kind"] == kind
        ]
        return copy.deepcopy(max(matches, key=lambda row: row["sequence"])) if matches else None

    def get_request(self, project, request_key):
        return copy.deepcopy(self.requests.get((project, request_key)))

    def add_request(self, project, request_key, fingerprint, record_id):
        self.requests[(project, request_key)] = {
            "project_id": project,
            "request_key": request_key,
            "fingerprint": fingerprint,
            "record_id": record_id,
        }

    def lock_request(self, value):
        return None

    def try_job_lock(self, value):
        return True

    def unlock_job(self, value):
        return None

    def pending_job_ids(self):
        return [
            row["id"]
            for row in self.records.values()
            if (
                row["kind"] in ("chat", "run")
                and row["data"].get("status") in ("queued", "running")
            )
            or (
                row["kind"] == "content_insight_artifact"
                and row["data"].get("status") in ("QUEUED", "RUNNING")
            )
        ]

    def delete_run_checkpoints(self, run_id):
        return None

    def delete_record(self, record_id, project, kind):
        self.get(record_id, project, kind=kind)
        del self.records[record_id]

    @staticmethod
    def public(row):
        return {"id": row["id"], "revision": row["revision"], **copy.deepcopy(row["data"])}

    def ready(self):
        return True, "ready"


def context(title="테스트 제품"):
    return {
        "project": {
            "business_type": "SOLE",
            "category": {"major": "테크·가전", "minor": "생활가전"},
            "title": title,
            "goal_amount": 3_000_000,
        },
        "rewards": [
            {
                "reward_id": 1,
                "name": "얼리버드",
                "description": "본품 1대",
                "price": 129_000,
                "is_limited": True,
                "quantity": 100,
                "is_early_bird": True,
                "options": [],
            }
        ],
        "source_images": [],
    }


def complete_review():
    return {
        "reply": "정리가 완료되었습니다.",
        "product": "작은 공간용 생활가전",
        "story": "생활 공간의 불편을 줄이는 제작 이야기",
        "strengths": [
            {"id": str(index), "title": f"강점 {index}", "description": f"설명 {index}"}
            for index in range(1, 4)
        ],
        "problems": [{"heading": f"문제 {index}", "body": f"상황 {index}"} for index in range(1, 5)],
        "missing": [],
        "tone": None,
        "brand_color": None,
    }


def confirmed_session(application, project="project-1"):
    session = application.create_session(project, SessionCreateRequest(context=context()))
    chat, should_dispatch = application.start_session(session["session_id"], project)
    assert should_dispatch is True
    application.complete_chat(
        session_id=session["session_id"],
        chat_id=chat["chat_id"],
        project=project,
        source_revision=1,
        review=complete_review(),
        reply="정리가 완료되었습니다.",
    )
    application.confirm_session(session["session_id"], ConfirmRequest(revision=2), project)
    return session["session_id"]


def test_session_to_run_is_idempotent_without_persisting_result_snapshot():
    repository = FakeRecordRepository()
    application = FundingStoryApplication(repository)
    session_id = confirmed_session(application)
    request = RunRequest(
        session_id=session_id,
        confirmed_revision=2,
        idempotency_key="create-once",
        context=context(),
    )

    run, should_dispatch = application.create_run(request, "project-1")
    duplicate, duplicate_dispatch = application.create_run(request, "project-1")

    assert should_dispatch is True
    assert duplicate_dispatch is False
    assert duplicate["run_id"] == run["run_id"]
    stored = repository.get(run["run_id"])["data"]
    assert set(stored) == {"status", "session_id", "confirmed_revision", "error"}
    assert application.pending_job_ids() == [run["run_id"]]


def test_active_run_prevents_context_replacement_and_is_cleared_after_delivery():
    repository = FakeRecordRepository()
    application = FundingStoryApplication(repository)
    session_id = confirmed_session(application)
    first = RunRequest(
        session_id=session_id,
        confirmed_revision=2,
        idempotency_key="first",
        context=context(),
    )
    run, _ = application.create_run(first, "project-1")

    with pytest.raises(ApplicationConflict, match="전체 생성 작업"):
        application.create_run(
            RunRequest(
                session_id=session_id,
                confirmed_revision=2,
                idempotency_key="second",
                context=context("변경된 제품"),
            ),
            "project-1",
        )

    application.complete_run_delivery(run["run_id"], "succeeded")
    second, dispatched = application.create_run(
        RunRequest(
            session_id=session_id,
            confirmed_revision=2,
            idempotency_key="second",
            context=context("변경된 제품"),
        ),
        "project-1",
    )
    assert dispatched is True and second["run_id"] != run["run_id"]


def test_same_idempotency_key_with_different_context_conflicts():
    repository = FakeRecordRepository()
    application = FundingStoryApplication(repository)
    session_id = confirmed_session(application)
    application.create_run(
        RunRequest(
            session_id=session_id,
            confirmed_revision=2,
            idempotency_key="same-key",
            context=context(),
        ),
        "project-1",
    )

    with pytest.raises(ApplicationConflict, match="중복 키"):
        application.create_run(
            RunRequest(
                session_id=session_id,
                confirmed_revision=2,
                idempotency_key="same-key",
                context=context("다른 제품"),
            ),
            "project-1",
        )
