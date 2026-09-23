import copy
import threading
import time
from contextlib import contextmanager
from functools import lru_cache
from uuid import uuid4

from ..config import settings
from .persistence import PostgresRecordRepository, repository


def _is_pending(row: dict) -> bool:
    return row["kind"] in ("chat", "run") and row["data"].get("status") in ("queued", "running")


class InMemoryTtlRecordRepository:
    """Test adapter with the same short-lived semantics as the PostgreSQL store."""

    def __init__(self, ttl_seconds=86_400):
        self._ttl = ttl_seconds
        self.records: dict[str, dict] = {}
        self.requests: dict[tuple[str, str], dict] = {}
        self._latest: dict[tuple[str, str], str] = {}
        self._mutex = threading.RLock()

    @contextmanager
    def transaction(self):
        with self._mutex:
            yield self

    def create(self, project, kind, data, record_id=None):
        record_id = record_id or str(uuid4())
        now = time.time()
        self.records[record_id] = {
            "id": record_id,
            "project_id": project,
            "kind": kind,
            "revision": 1,
            "data": copy.deepcopy(data),
            "created_at": now,
            "updated_at": now,
        }
        self._latest[(project, kind)] = record_id
        return record_id

    def get(self, record_id, project=None, *, lock=False, kind=None):
        row = self.records.get(record_id)
        if not row or (project is not None and row["project_id"] != project) or (
            kind is not None and row["kind"] != kind
        ):
            raise LookupError("대상을 찾을 수 없습니다.")
        return copy.deepcopy(row)

    def save(self, record_id, data, revision=None):
        row = self.records.get(record_id)
        if not row:
            raise LookupError("대상을 찾을 수 없습니다.")
        row["data"] = copy.deepcopy(data)
        if revision is not None:
            row["revision"] = revision
        row["updated_at"] = time.time()
        self._latest[(row["project_id"], row["kind"])] = record_id

    def latest(self, project, kind):
        record_id = self._latest.get((project, kind))
        return self.get(record_id, project, kind=kind) if record_id else None

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

    def pending_job_ids(self):
        now = time.time()
        return [
            record_id
            for record_id, row in self.records.items()
            if _is_pending(row)
            and (
                row["data"].get("status") == "queued"
                or float(row["data"].get("_lease_until", 0)) <= now
            )
        ]

    def cleanup_expired(self):
        cutoff = time.time() - self._ttl
        expired = [record_id for record_id, row in self.records.items() if row["updated_at"] < cutoff]
        for record_id in expired:
            row = self.records.pop(record_id)
            self.requests = {
                key: value
                for key, value in self.requests.items()
                if value["record_id"] != record_id
            }
            if self._latest.get((row["project_id"], row["kind"])) == record_id:
                self._latest.pop((row["project_id"], row["kind"]), None)
        return len(expired)

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


class PostgresTtlRecordRepository:
    """Funding Story view over the shared AI PostgreSQL tables with TTL semantics."""

    _KINDS = ("session", "chat", "run")
    _TTL_KINDS = _KINDS + ("image_rate_limit",)

    def __init__(self, records: PostgresRecordRepository, ttl_seconds: int):
        self._records = records
        self._ttl = ttl_seconds

    def _expired(self, row):
        return row["updated_at"].timestamp() < time.time() - self._ttl

    @contextmanager
    def transaction(self):
        with self._records.transaction() as records:
            yield PostgresTtlRecordRepository(records, self._ttl)

    def create(self, project, kind, data, record_id=None):
        return self._records.create(project, kind, data, record_id)

    def get(self, record_id, project=None, *, lock=False, kind=None):
        row = self._records.get(record_id, project, lock=lock, kind=kind)
        if row["kind"] in self._KINDS and self._expired(row):
            raise LookupError("대상을 찾을 수 없습니다.")
        return row

    def save(self, record_id, data, revision=None):
        return self._records.save(record_id, data, revision)

    def latest(self, project, kind):
        row = self._records.latest(project, kind)
        return None if row is None or self._expired(row) else row

    def get_request(self, project, request_key):
        request = self._records.get_request(project, request_key)
        if request is None:
            return None
        try:
            self.get(request["record_id"], project)
        except LookupError:
            return None
        return request

    def add_request(self, project, request_key, fingerprint, record_id):
        return self._records.add_request(project, request_key, fingerprint, record_id)

    def lock_request(self, value):
        return self._records.lock_request(value)

    def pending_job_ids(self):
        pending = []
        for record_id in self._records.pending_job_ids():
            try:
                row = self.get(record_id)
            except LookupError:
                continue
            if row["kind"] in ("chat", "run") and _is_pending(row):
                pending.append(record_id)
        return pending

    def cleanup_expired(self):
        return self._records.delete_expired(self._TTL_KINDS, self._ttl)

    def delete_run_checkpoints(self, run_id):
        return self._records.delete_run_checkpoints(run_id)

    def delete_record(self, record_id, project, kind):
        return self._records.delete_record(record_id, project, kind)

    @staticmethod
    def public(row):
        return {"id": row["id"], "revision": row["revision"], **copy.deepcopy(row["data"])}

    def ready(self):
        return self._records.ready()


@lru_cache
def ttl_repository():
    cfg = settings()
    if cfg.app_env == "test":
        return InMemoryTtlRecordRepository(cfg.funding_story_state_ttl_seconds)
    return PostgresTtlRecordRepository(repository(), cfg.funding_story_state_ttl_seconds)
