from contextlib import contextmanager
from functools import lru_cache
from uuid import uuid4

from psycopg.types.json import Jsonb

from .database import connection
from .schema import inspect_connection

EXPECTED_SCHEMA_VERSION = "2"


class PostgresRecordRepository:
    def __init__(self, conn=None):
        self._conn = conn

    @contextmanager
    def _using_connection(self):
        if self._conn is not None:
            yield self._conn
        else:
            with connection() as conn:
                yield conn

    @contextmanager
    def transaction(self):
        if self._conn is not None:
            yield self
            return
        with connection() as conn:
            yield PostgresRecordRepository(conn)

    def create(self, project, kind, data, record_id=None):
        rid = record_id or str(uuid4())
        with self._using_connection() as conn:
            conn.execute(
                "INSERT INTO ai_records(id,project_id,kind,data) VALUES(%s,%s,%s,%s)",
                (rid, project, kind, Jsonb(data)),
            )
        return rid

    def get(self, record_id, project=None, *, lock=False, kind=None):
        with self._using_connection() as conn:
            row = conn.execute(
                "SELECT * FROM ai_records WHERE id=%s" + (" FOR UPDATE" if lock else ""),
                (record_id,),
            ).fetchone()
        if not row or (project is not None and row["project_id"] != project) or (kind and row["kind"] != kind):
            raise LookupError("대상을 찾을 수 없습니다.")
        return row

    def save(self, record_id, data, revision=None):
        with self._using_connection() as conn:
            conn.execute(
                "UPDATE ai_records SET data=%s,revision=COALESCE(%s,revision),updated_at=now() WHERE id=%s",
                (Jsonb(data), revision, record_id),
            )

    def latest(self, project, kind):
        with self._using_connection() as conn:
            return conn.execute(
                "SELECT * FROM ai_records WHERE project_id=%s AND kind=%s "
                "ORDER BY updated_at DESC, id DESC LIMIT 1",
                (project, kind),
            ).fetchone()

    def get_request(self, project, request_key):
        with self._using_connection() as conn:
            return conn.execute(
                "SELECT * FROM ai_requests WHERE project_id=%s AND request_key=%s",
                (project, request_key),
            ).fetchone()

    def add_request(self, project, request_key, fingerprint, record_id):
        with self._using_connection() as conn:
            conn.execute(
                "INSERT INTO ai_requests(project_id,request_key,fingerprint,record_id) "
                "VALUES(%s,%s,%s,%s) "
                "ON CONFLICT(project_id,request_key) DO UPDATE SET "
                "fingerprint=EXCLUDED.fingerprint,record_id=EXCLUDED.record_id",
                (project, request_key, fingerprint, record_id),
            )

    def lock_request(self, value):
        with self._using_connection() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (value,))

    def pending_job_ids(self):
        with self._using_connection() as conn:
            rows = conn.execute(
                "SELECT id FROM ai_records "
                "WHERE ((kind IN ('chat','run') AND data->>'status' IN ('queued','running')) "
                "OR (kind='content_insight_artifact' AND data->>'status' IN ('QUEUED','RUNNING'))) "
                "AND (data->>'status' IN ('queued','QUEUED') "
                "OR COALESCE((data->>'_lease_until')::double precision,0) "
                "<= EXTRACT(EPOCH FROM clock_timestamp()))"
            ).fetchall()
        return [row["id"] for row in rows]

    def delete_expired(self, kinds, ttl_seconds):
        with self._using_connection() as conn:
            rows = conn.execute(
                "SELECT id FROM ai_records WHERE kind = ANY(%s) "
                "AND updated_at < clock_timestamp() - (%s * interval '1 second')",
                (list(kinds), ttl_seconds),
            ).fetchall()
            record_ids = [row["id"] for row in rows]
            if not record_ids:
                return 0
            conn.execute("DELETE FROM ai_requests WHERE record_id = ANY(%s)", (record_ids,))
            result = conn.execute("DELETE FROM ai_records WHERE id = ANY(%s)", (record_ids,))
        return result.rowcount

    def delete_run_checkpoints(self, run_id):
        pattern = run_id + ":%"
        with self._using_connection() as conn:
            for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                conn.execute(f"DELETE FROM {table} WHERE thread_id LIKE %s", (pattern,))

    def delete_record(self, record_id, project, kind):
        with self._using_connection() as conn:
            conn.execute("DELETE FROM ai_requests WHERE record_id=%s", (record_id,))
            result = conn.execute(
                "DELETE FROM ai_records WHERE id=%s AND project_id=%s AND kind=%s",
                (record_id, project, kind),
            )
        if result.rowcount != 1:
            raise LookupError("삭제할 대상을 찾을 수 없습니다.")

    @staticmethod
    def public(row):
        return {"id": row["id"], "revision": row["revision"], **row["data"]}

    def ready(self):
        try:
            with self._using_connection() as conn:
                conn.execute("SELECT 1").fetchone()
                row = conn.execute(
                    "SELECT version FROM flyway_schema_history "
                    "WHERE success=true AND version IS NOT NULL ORDER BY installed_rank DESC LIMIT 1"
                ).fetchone()
            if not row or row["version"] != EXPECTED_SCHEMA_VERSION:
                return False, "database migration is not current"
            with self._using_connection() as conn:
                if inspect_connection(conn):
                    return False, "database schema does not match the application"
            return True, "ready"
        except Exception as exc:  # noqa: BLE001 - readiness must return a safe status
            return False, type(exc).__name__


@lru_cache
def repository() -> PostgresRecordRepository:
    return PostgresRecordRepository()
