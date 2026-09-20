import copy
import json
import threading
import time
from contextlib import contextmanager
from functools import lru_cache
from uuid import uuid4

from redis import Redis

from ..config import settings


def _is_pending(row: dict) -> bool:
    return row["kind"] in ("chat", "run") and row["data"].get("status") in ("queued", "running")


class InMemoryTtlRecordRepository:
    """Test adapter with the same non-durable semantics as the Redis state store."""

    def __init__(self):
        self.records: dict[str, dict] = {}
        self.requests: dict[tuple[str, str], dict] = {}
        self._latest: dict[tuple[str, str], str] = {}
        self._locks: set[str] = set()
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

    def try_job_lock(self, value):
        if value in self._locks:
            return False
        self._locks.add(value)
        return True

    def unlock_job(self, value):
        self._locks.discard(value)

    def pending_job_ids(self):
        return [record_id for record_id, row in self.records.items() if _is_pending(row)]

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


class RedisTtlRecordRepository:
    """Short-lived Funding Story state shared by API and Celery workers."""

    def __init__(self, client: Redis, ttl_seconds: int):
        self._redis = client
        self._ttl = ttl_seconds
        self._prefix = "funding-story:v1"
        self._job_tokens: dict[str, str] = {}

    def _record_key(self, record_id):
        return f"{self._prefix}:record:{record_id}"

    def _latest_key(self, project, kind):
        return f"{self._prefix}:latest:{project}:{kind}"

    def _request_key(self, project, request_key):
        return f"{self._prefix}:request:{project}:{request_key}"

    @contextmanager
    def transaction(self):
        lock = self._redis.lock(
            f"{self._prefix}:transaction",
            timeout=30,
            blocking_timeout=10,
        )
        if not lock.acquire(blocking=True):
            raise TimeoutError("Funding Story 상태 잠금을 획득하지 못했습니다.")
        try:
            yield self
        finally:
            lock.release()

    def _write(self, row):
        key = self._record_key(row["id"])
        pipe = self._redis.pipeline()
        pipe.set(key, json.dumps(row, ensure_ascii=False, separators=(",", ":")), ex=self._ttl)
        pipe.set(self._latest_key(row["project_id"], row["kind"]), row["id"], ex=self._ttl)
        if _is_pending(row):
            pipe.sadd(f"{self._prefix}:pending", row["id"])
        else:
            pipe.srem(f"{self._prefix}:pending", row["id"])
        pipe.execute()

    def create(self, project, kind, data, record_id=None):
        record_id = record_id or str(uuid4())
        now = time.time()
        row = {
            "id": record_id,
            "project_id": project,
            "kind": kind,
            "revision": 1,
            "data": data,
            "created_at": now,
            "updated_at": now,
        }
        if not self._redis.set(
            self._record_key(record_id),
            json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            ex=self._ttl,
            nx=True,
        ):
            raise ValueError("이미 존재하는 상태 ID입니다.")
        self._write(row)
        return record_id

    def get(self, record_id, project=None, *, lock=False, kind=None):
        raw = self._redis.get(self._record_key(record_id))
        if raw is None:
            raise LookupError("대상을 찾을 수 없습니다.")
        row = json.loads(raw)
        if (project is not None and row["project_id"] != project) or (
            kind is not None and row["kind"] != kind
        ):
            raise LookupError("대상을 찾을 수 없습니다.")
        return row

    def save(self, record_id, data, revision=None):
        row = self.get(record_id)
        row["data"] = data
        if revision is not None:
            row["revision"] = revision
        row["updated_at"] = time.time()
        self._write(row)

    def latest(self, project, kind):
        record_id = self._redis.get(self._latest_key(project, kind))
        if not record_id:
            return None
        try:
            return self.get(record_id, project, kind=kind)
        except LookupError:
            self._redis.delete(self._latest_key(project, kind))
            return None

    def get_request(self, project, request_key):
        raw = self._redis.get(self._request_key(project, request_key))
        return json.loads(raw) if raw else None

    def add_request(self, project, request_key, fingerprint, record_id):
        value = {
            "project_id": project,
            "request_key": request_key,
            "fingerprint": fingerprint,
            "record_id": record_id,
        }
        self._redis.set(
            self._request_key(project, request_key),
            json.dumps(value, ensure_ascii=False, separators=(",", ":")),
            ex=self._ttl,
        )

    def lock_request(self, value):
        return None

    def try_job_lock(self, value):
        token = str(uuid4())
        acquired = bool(
            self._redis.set(f"{self._prefix}:job-lock:{value}", token, ex=600, nx=True)
        )
        if acquired:
            self._job_tokens[value] = token
        return acquired

    def unlock_job(self, value):
        token = self._job_tokens.pop(value, None)
        if token is None:
            return
        self._redis.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end",
            1,
            f"{self._prefix}:job-lock:{value}",
            token,
        )

    def pending_job_ids(self):
        pending = []
        key = f"{self._prefix}:pending"
        for record_id in self._redis.smembers(key):
            try:
                row = self.get(record_id)
            except LookupError:
                self._redis.srem(key, record_id)
                continue
            if _is_pending(row):
                pending.append(record_id)
            else:
                self._redis.srem(key, record_id)
        return pending

    def delete_run_checkpoints(self, run_id):
        return None

    def delete_record(self, record_id, project, kind):
        row = self.get(record_id, project, kind=kind)
        pipe = self._redis.pipeline()
        pipe.delete(self._record_key(record_id))
        pipe.srem(f"{self._prefix}:pending", record_id)
        if self._redis.get(self._latest_key(project, kind)) == record_id:
            pipe.delete(self._latest_key(project, kind))
        pipe.execute()
        return row

    @staticmethod
    def public(row):
        return {"id": row["id"], "revision": row["revision"], **copy.deepcopy(row["data"])}

    def ready(self):
        try:
            self._redis.ping()
            return True, "ready"
        except Exception as exc:  # noqa: BLE001
            return False, type(exc).__name__


@lru_cache
def ttl_repository():
    cfg = settings()
    if cfg.app_env == "test":
        return InMemoryTtlRecordRepository()
    return RedisTtlRecordRepository(
        Redis.from_url(cfg.funding_story_state_redis_url, decode_responses=True),
        cfg.funding_story_state_ttl_seconds,
    )
