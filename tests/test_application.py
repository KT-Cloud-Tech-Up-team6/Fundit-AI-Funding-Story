import copy
from contextlib import contextmanager

import pytest

from funding_story.application import ApplicationConflict, FundingStoryApplication
from funding_story.models import ConfirmRequest, ProjectInput, RunRequest


class FakeRecordRepository:
    """In-memory domain-port adapter used to test use cases without PostgreSQL."""

    def __init__(self):
        self.records = {}
        self.requests = {}
        self.sequence = 0

    @contextmanager
    def transaction(self):
        yield self

    def create(self, project, kind, data, record_id=None):
        self.sequence += 1
        rid = record_id or f"record-{self.sequence}"
        self.records[rid] = {
            "id": rid,
            "project_id": project,
            "kind": kind,
            "revision": 1,
            "data": copy.deepcopy(data),
            "sequence": self.sequence,
        }
        return rid

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
            if row["kind"] in ("chat", "run") and row["data"].get("status") in ("queued", "running")
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


def complete_review():
    return {
        "reply": "정리가 완료되었습니다.",
        "strengths": [
            {"id": "one", "title": "강점 1", "description": "설명 1"},
            {"id": "two", "title": "강점 2", "description": "설명 2"},
            {"id": "three", "title": "강점 3", "description": "설명 3"},
        ],
        "problems": [{"heading": f"문제 {index}", "body": f"상황 {index}"} for index in range(1, 5)],
        "missing": [],
    }


def test_session_to_run_use_case_without_database():
    repository = FakeRecordRepository()
    application = FundingStoryApplication(repository)
    project = "project-1"

    session = application.create_session(
        project,
        ProjectInput(title="테스트 제품"),
        lambda asset_id, project_id: None,
    )
    chat, should_dispatch = application.start_session(session["id"], project)
    duplicate, duplicate_dispatch = application.start_session(session["id"], project)

    assert should_dispatch is True
    assert duplicate_dispatch is False
    assert duplicate["id"] == chat["id"]

    application.complete_chat(
        session_id=session["id"],
        chat_id=chat["id"],
        project=project,
        source_revision=2,
        input_data=ProjectInput(title="테스트 제품").model_dump(),
        review=complete_review(),
        reply="정리가 완료되었습니다.",
    )
    assert application.confirm_session(session["id"], ConfirmRequest(revision=3), project) == {
        "confirmed_revision": 3
    }

    request = RunRequest(session_id=session["id"], revision=3, idempotency_key="create-once")
    run, should_dispatch = application.create_run(request, project)
    duplicate_run, duplicate_dispatch = application.create_run(request, project)

    assert should_dispatch is True
    assert duplicate_dispatch is False
    assert duplicate_run["id"] == run["id"]
    assert application.pending_job_ids() == [run["id"]]


def test_run_idempotency_conflict_is_an_application_error():
    repository = FakeRecordRepository()
    application = FundingStoryApplication(repository)
    project = "project-1"
    session_id = repository.create(
        project,
        "session",
        {
            "input": ProjectInput(title="테스트 제품").model_dump(),
            "messages": [],
            "review": complete_review(),
            "confirmed_revision": 1,
        },
    )
    application.create_run(RunRequest(session_id=session_id, revision=1, idempotency_key="same-key"), project)

    with pytest.raises(ApplicationConflict, match="중복 키"):
        application.create_run(
            RunRequest(session_id=session_id, revision=2, idempotency_key="same-key"), project
        )
