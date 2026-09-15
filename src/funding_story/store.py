import json
from contextlib import contextmanager
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import settings

DDL = """
CREATE TABLE IF NOT EXISTS ai_records(
 id text PRIMARY KEY, project_id text NOT NULL, kind text NOT NULL,
 revision integer NOT NULL DEFAULT 1, data jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS ai_records_project ON ai_records(project_id,kind);
CREATE TABLE IF NOT EXISTS ai_requests(
 project_id text NOT NULL, request_key text NOT NULL, fingerprint text NOT NULL,
 record_id text NOT NULL REFERENCES ai_records(id), PRIMARY KEY(project_id,request_key));
"""


@contextmanager
def connection():
    with psycopg.connect(settings().database_url, row_factory=dict_row) as conn:
        yield conn


def initialize():
    from langgraph.checkpoint.postgres import PostgresSaver

    with connection() as conn:
        conn.execute(DDL)
    with PostgresSaver.from_conn_string(settings().database_url) as saver:
        saver.setup()


def create(project, kind, data, conn=None, record_id=None):
    rid = record_id or str(uuid4())
    if conn is None:
        with connection() as own:
            return create(project, kind, data, own, rid)
    conn.execute(
        "INSERT INTO ai_records(id,project_id,kind,data) VALUES(%s,%s,%s,%s)",
        (rid, project, kind, Jsonb(data)),
    )
    return rid


def get(rid, project=None, conn=None, lock=False, kind=None):
    if conn is None:
        with connection() as own:
            return get(rid, project, own, lock, kind)
    row = conn.execute(
        "SELECT * FROM ai_records WHERE id=%s" + (" FOR UPDATE" if lock else ""), (rid,)
    ).fetchone()
    if not row or (project is not None and row["project_id"] != project) or (kind and row["kind"] != kind):
        raise LookupError("대상을 찾을 수 없습니다.")
    return row


def save(rid, data, conn=None, revision=None):
    if conn is None:
        with connection() as own:
            return save(rid, data, own, revision)
    conn.execute(
        "UPDATE ai_records SET data=%s,revision=COALESCE(%s,revision),updated_at=now() WHERE id=%s",
        (Jsonb(data), revision, rid),
    )


def public(row):
    return {"id": row["id"], "revision": row["revision"], **row["data"]}


def emit(event, **fields):
    # Intentionally excludes prompts, provider credentials and signed URLs.
    print(json.dumps({"event": event, **fields}, ensure_ascii=False), flush=True)


def delete_run_checkpoints(run_id):
    pattern = run_id + ":%"
    with connection() as conn:
        for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            conn.execute(f"DELETE FROM {table} WHERE thread_id LIKE %s", (pattern,))


def delete_record(rid, project, kind):
    with connection() as conn:
        result = conn.execute(
            "DELETE FROM ai_records WHERE id=%s AND project_id=%s AND kind=%s",
            (rid, project, kind),
        )
        if result.rowcount != 1:
            raise LookupError("삭제할 대상을 찾을 수 없습니다.")


if __name__ == "__main__":
    initialize()
