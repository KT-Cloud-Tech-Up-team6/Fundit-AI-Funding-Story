from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from testcontainers.community.postgres import PostgresContainer

from funding_story.infrastructure.persistence import PostgresRecordRepository
from funding_story.migration import baseline_existing_database, inspect_schema

MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migration"


def test_existing_schema_can_be_verified_and_baselined():
    with PostgresContainer(
        "postgres:17", username="funding_ai", password="test-only", dbname="funding_ai_legacy"
    ) as legacy:
        dsn = legacy.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            assert PostgresRecordRepository(conn).ready()[0] is False
        with psycopg.connect(dsn) as conn:
            for migration in sorted(MIGRATIONS.glob("V*.sql")):
                conn.execute(migration.read_text())

        assert inspect_schema(dsn) == []
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            assert PostgresRecordRepository(conn).ready()[0] is False
        baseline_existing_database(dsn, network_container=legacy.get_wrapped_container().id)

        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            row = conn.execute(
                "SELECT version,type,description,success FROM flyway_schema_history"
            ).fetchone()
            assert PostgresRecordRepository(conn).ready() == (True, "ready")
        assert tuple(row.values()) == ("2", "BASELINE", "existing_schema", True)
