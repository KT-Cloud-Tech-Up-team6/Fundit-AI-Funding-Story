from types import SimpleNamespace

from funding_story import tasks
from funding_story.content_insights import api
from funding_story.content_insights.models import ArtifactType


def test_dispatch_routes_artifacts_to_dedicated_queues(monkeypatch):
    artifact_calls = []
    authoring_calls = []
    monkeypatch.setattr(tasks.application, "pending_job_ids", lambda: ["authoring"])
    monkeypatch.setattr(
        tasks.content_insights_application,
        "pending_artifacts",
        lambda: [
            ("page", ArtifactType.PAGE_SUMMARY),
            ("storyline", ArtifactType.STORYLINE),
        ],
    )
    monkeypatch.setattr(
        tasks,
        "enqueue_content_insight",
        lambda artifact_id, artifact_type: artifact_calls.append((artifact_id, artifact_type)),
    )
    monkeypatch.setattr(tasks.execute, "delay", authoring_calls.append)

    tasks.dispatch()

    assert artifact_calls == [
        ("page", ArtifactType.PAGE_SUMMARY),
        ("storyline", ArtifactType.STORYLINE),
    ]
    assert authoring_calls == ["authoring"]


def test_queue_names_are_configurable_per_artifact(monkeypatch):
    monkeypatch.setattr(
        tasks,
        "settings",
        lambda: SimpleNamespace(
            content_insights_page_summary_queue="required-page",
            content_insights_storyline_queue="required-storyline",
        ),
    )
    assert tasks.content_insight_queue(ArtifactType.PAGE_SUMMARY) == "required-page"
    assert tasks.content_insight_queue(ArtifactType.STORYLINE) == "required-storyline"


def test_broker_failure_leaves_artifact_for_durable_dispatch(monkeypatch):
    events = []

    def unavailable(*args):
        raise ConnectionError("broker unavailable")

    monkeypatch.setattr(tasks, "enqueue_content_insight", unavailable)
    monkeypatch.setattr(api, "emit", lambda event, **fields: events.append((event, fields)))

    api.kick("artifact-1", ArtifactType.PAGE_SUMMARY)

    assert events == [
        (
            "content_insight_outbox_waiting",
            {"artifact_id": "artifact-1", "artifact_type": "PAGE_SUMMARY"},
        )
    ]
