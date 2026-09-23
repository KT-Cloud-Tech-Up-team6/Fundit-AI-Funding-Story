from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from psycopg.conninfo import conninfo_to_dict

from funding_story.config import Settings
from funding_story.infrastructure.persistence import connection, repository
from funding_story.infrastructure.ttl_state import PostgresTtlRecordRepository
from funding_story.job_lease import leased_record
from funding_story.migration import run_flyway


def test_readiness_rejects_schema_drift_with_current_flyway_version(postgres_container):
    from funding_story.infrastructure.persistence import PostgresRecordRepository

    with connection() as conn:
        conn.execute("ALTER TABLE ai_records ALTER COLUMN revision DROP NOT NULL")
        assert PostgresRecordRepository(conn).ready() == (
            False,
            "database schema does not match the application",
        )
        conn.rollback()
    assert repository().ready() == (True, "ready")


def test_runtime_startup_rejects_invalid_schema(monkeypatch):
    from funding_story import bootstrap, tasks

    monkeypatch.setattr(bootstrap, "_open_pools", lambda **kwargs: None)
    monkeypatch.setattr(bootstrap, "close_pools", lambda: None)
    monkeypatch.setattr(bootstrap.application, "readiness", lambda: (False, "schema mismatch"))
    with pytest.raises(RuntimeError, match="schema mismatch"):
        bootstrap.open_pools()
    with pytest.raises(SystemExit, match="schema mismatch"):
        tasks.open_worker_database_pools()


def test_production_rejects_incomplete_dsn(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="비어"):
        Settings(
            _env_file=None,
            app_env="prod",
            ai_service_token="test-only",
            database_url="postgresql://db.example/funding_ai",
        )


def test_migration_preserves_sslmode(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "funding_story.migration.subprocess.run", lambda *args, **kwargs: calls.append(kwargs)
    )
    run_flyway(dsn="postgresql://ai:test-only@db.example/funding_ai?sslmode=verify-full")
    assert calls[0]["env"]["FLYWAY_URL"].endswith("?sslmode=verify-full")


def test_flyway_and_checkpoint_schema_are_current(postgres_container):
    run_flyway(network_container=postgres_container.get_wrapped_container().id)

    with connection() as conn:
        history = conn.execute(
            "SELECT version,success FROM flyway_schema_history "
            "WHERE version IS NOT NULL ORDER BY installed_rank"
        ).fetchall()
        checkpoint_versions = {
            row["v"] for row in conn.execute("SELECT v FROM checkpoint_migrations").fetchall()
        }
        task_path = conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name='checkpoint_writes' "
            "AND column_name='task_path'"
        ).fetchone()

    assert [(row["version"], row["success"]) for row in history] == [("1", True), ("2", True)]
    assert checkpoint_versions == set(range(10))
    assert task_path is not None
    assert repository().ready() == (True, "ready")


def test_runtime_pool_supports_concurrent_queries(postgres_container):
    def query(value):
        with connection() as conn:
            return conn.execute("SELECT %s::integer AS value", (value,)).fetchone()["value"]

    with ThreadPoolExecutor(max_workers=10) as workers:
        assert list(workers.map(query, range(50))) == list(range(50))


def test_postgres_job_lease_prevents_duplicate_work_without_holding_transaction(postgres_container):
    records = PostgresTtlRecordRepository(repository(), 300)
    record_id = records.create(str(uuid4()), "chat", {"status": "queued"})

    with leased_record(records, record_id, queued="queued", running="running") as claimed:
        assert claimed is not None
        with leased_record(records, record_id, queued="queued", running="running") as duplicate:
            assert duplicate is None
        current = records.get(record_id)
        current["data"]["status"] = "succeeded"
        records.save(record_id, current["data"])

    stored = records.get(record_id)
    assert stored["data"]["status"] == "succeeded"
    assert "_lease_token" not in stored["data"]


def test_postgres_ttl_cleanup_releases_idempotency_key(postgres_container):
    records = PostgresTtlRecordRepository(repository(), 300)
    project = str(uuid4())
    record_id = records.create(project, "session", {"status": "ready"})
    records.add_request(project, "request-1", "fingerprint", record_id)
    with connection() as conn:
        conn.execute(
            "UPDATE ai_records SET updated_at=clock_timestamp() - interval '301 seconds' WHERE id=%s",
            (record_id,),
        )

    assert records.get_request(project, "request-1") is None
    assert records.cleanup_expired() == 1
    with connection() as conn:
        assert conn.execute("SELECT 1 FROM ai_requests WHERE record_id=%s", (record_id,)).fetchone() is None


def test_local_database_defaults_use_the_ai_port(monkeypatch):
    for name in (
        "DATABASE_URL",
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "DB_USERNAME",
        "DB_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)
    cfg = Settings(_env_file=None, ai_service_token="test-only")
    assert cfg.db_port == 5440
    assert "port=5440" in cfg.database_dsn


def test_database_dsn_preserves_special_characters():
    cfg = Settings(
        _env_file=None,
        database_url=None,
        db_password="space @ quote' equals=slash/",
        ai_service_token="test-only",
    )
    assert conninfo_to_dict(cfg.database_dsn)["password"] == "space @ quote' equals=slash/"


def test_production_rejects_local_database_defaults():
    with pytest.raises(ValueError, match="localhost"):
        Settings(
            _env_file=None,
            app_env="prod",
            database_url=None,
            db_host="localhost",
            db_password="local-ai-only",
            ai_service_token="test-only",
        )


def test_production_rejects_local_database_url_override():
    with pytest.raises(ValueError, match="localhost"):
        Settings(
            _env_file=None,
            app_env="prod",
            database_url="postgresql://user:secret@localhost:5432/funding_ai",
            ai_service_token="test-only",
        )
