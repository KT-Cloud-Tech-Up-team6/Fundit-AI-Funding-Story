from funding_story import worker
from funding_story.content_insights import api
from funding_story.content_insights.models import ArtifactType


def test_polling_lanes_select_only_their_jobs(monkeypatch):
    monkeypatch.setattr(worker.application, "pending_job_ids", lambda: ["authoring"])
    monkeypatch.setattr(
        worker.content_insights_application,
        "pending_artifacts",
        lambda: [
            ("page", ArtifactType.PAGE_SUMMARY),
            ("storyline", ArtifactType.STORYLINE),
        ],
    )

    assert [job.record_id for job in worker.pending_jobs("all")] == [
        "authoring",
        "page",
        "storyline",
    ]
    assert [job.record_id for job in worker.pending_jobs("funding-story")] == ["authoring"]
    assert [job.record_id for job in worker.pending_jobs("page-summary")] == ["page"]
    assert [job.record_id for job in worker.pending_jobs("storyline")] == ["storyline"]


def test_polling_worker_routes_each_job_type(monkeypatch):
    calls = []
    monkeypatch.setattr(worker, "execute", lambda record_id: calls.append(("funding-story", record_id)))
    monkeypatch.setattr(
        worker,
        "execute_content_insight",
        lambda record_id, artifact_type: calls.append((artifact_type.value, record_id)),
    )

    worker.run_job(worker.PendingJob("authoring", "funding-story"))
    worker.run_job(worker.PendingJob("page", "page-summary", ArtifactType.PAGE_SUMMARY))

    assert calls == [("funding-story", "authoring"), ("PAGE_SUMMARY", "page")]


def test_content_insight_kick_only_records_database_queue_event(monkeypatch):
    events = []
    monkeypatch.setattr(api, "emit", lambda event, **fields: events.append((event, fields)))

    api.kick("artifact-1", ArtifactType.PAGE_SUMMARY)

    assert events == [
        (
            "content_insight_job_queued",
            {"artifact_id": "artifact-1", "artifact_type": "PAGE_SUMMARY"},
        )
    ]
