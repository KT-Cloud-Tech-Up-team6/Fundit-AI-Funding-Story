"""Provide a disposable database only to tests that request it.

Tests deliberately ignore DATABASE_URL and TEST_DATABASE_URL from the developer's
shell so a local, shared or production database can never be selected accidentally.
"""

import os

import pytest

os.environ["APP_ENV"] = "test"
os.environ["AI_SERVICE_TOKEN"] = "test-only"
os.environ.setdefault("RYUK_CONTAINER_IMAGE", "testcontainers/ryuk:0.14.0")
os.environ.pop("DATABASE_URL", None)
os.environ.pop("TEST_DATABASE_URL", None)


@pytest.fixture(scope="session")
def postgres_container():
    from testcontainers.community.postgres import PostgresContainer

    postgres = PostgresContainer(
        "postgres:17",
        username="funding_ai",
        password="test-only",
        dbname="funding_ai_test",
    )
    from funding_story.config import settings
    from funding_story.migration import run_flyway

    try:
        postgres.start()
        os.environ["DATABASE_URL"] = postgres.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql://"
        )
        settings.cache_clear()
        run_flyway(
            dsn=os.environ["DATABASE_URL"],
            network_container=postgres.get_wrapped_container().id,
        )
        yield postgres
    finally:
        from funding_story.infrastructure.persistence import close_pools

        close_pools()
        postgres.stop()
