"""Tests never write to the live worker database."""

import os

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://funding_ai:local-ai-only@localhost:55432/funding_ai_test",
)
os.environ.setdefault("AI_SERVICE_TOKEN", "test-only")
os.environ["LANGSMITH_TRACING"] = "false"

from funding_story import store


def pytest_sessionstart(session):
    store.initialize()
