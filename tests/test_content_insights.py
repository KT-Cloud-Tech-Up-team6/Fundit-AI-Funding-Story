import time

import pytest
from test_application import FakeRecordRepository

from funding_story.application import ApplicationConflict, ApplicationInvalid
from funding_story.content_insights import (
    ArtifactOutput,
    ArtifactType,
    ContentInsightCreateRequest,
    ContentInsightRunResponse,
    ContentInsightsApplication,
)
from funding_story.content_insights.worker import execute_artifact


def request(revision=1, key="content-1", trigger="PROJECT_REGISTRATION_COMPLETED"):
    return ContentInsightCreateRequest(
        source_revision=revision,
        idempotency_key=key,
        trigger=trigger,
        project_snapshot={
            "title": "LUMI S1",
            "category": "테크·가전",
            "description": "약 1.3kg 본체와 세척 가능한 1차 필터를 갖춘 무선 청소기입니다.",
            "rewards": [
                {
                    "name": "얼리버드",
                    "description": "본체와 틈새 노즐로 구성됩니다.",
                    "price": 129000,
                }
            ],
            "story_content": [
                {"type": "TEXT", "value": "좁은 공간을 자주 청소하는 사용자를 위해 준비했습니다."}
            ],
        },
    )


def artifact_id(run, artifact_type):
    return run["artifacts"][artifact_type]["artifact_id"]


def complete(application, aid, text):
    with application.claim_artifact(aid) as claimed:
        assert claimed is not None
        application.complete_artifact(
            aid,
            ArtifactOutput(
                schema_version=2,
                sections=[
                    {
                        "role": "WHAT",
                        "headline": "프로젝트 제품",
                        "description": text,
                    },
                    {
                        "role": "WHY",
                        "headline": "사용 맥락",
                        "description": "확인된 제품의 사용 맥락을 전달하는 프로젝트",
                    },
                ],
                source_fields=["description"],
            ),
            prompt_version="test-v1",
            model="test-model",
        )


def complete_storyline(application, aid):
    with application.claim_artifact(aid) as claimed:
        assert claimed is not None
        application.complete_artifact(
            aid,
            ArtifactOutput(
                content="약 1.3kg 본체와 틈새 노즐을 포함하는 프로젝트입니다. 좁은 공간의 청소 부담을 줄입니다.",
                source_fields=["description", "story_content", "rewards"],
            ),
            prompt_version="storyline-v3",
            model="test-model",
        )


def test_page_summary_run_is_idempotent_and_does_not_queue_storyline():
    repository = FakeRecordRepository()
    application = ContentInsightsApplication(repository)

    run, dispatch = application.create_run(request(), "project-1", ArtifactType.PAGE_SUMMARY)
    duplicate, duplicate_dispatch = application.create_run(
        request(), "project-1", ArtifactType.PAGE_SUMMARY
    )

    parsed = ContentInsightRunResponse.model_validate(run)
    assert parsed.status.value == "QUEUED"
    assert parsed.artifacts[ArtifactType.PAGE_SUMMARY].required is True
    assert parsed.artifacts[ArtifactType.STORYLINE].status.value == "NOT_REQUESTED"
    assert {artifact_type for _, artifact_type in dispatch} == {ArtifactType.PAGE_SUMMARY}
    assert duplicate["run_id"] == run["run_id"] and duplicate_dispatch == []
    assert set(application.pending_job_ids()) == {artifact_id for artifact_id, _ in dispatch}

    changed = request()
    changed.project_snapshot.description = "다른 설명"
    with pytest.raises(ApplicationConflict, match="중복 키"):
        application.create_run(changed, "project-1", ArtifactType.PAGE_SUMMARY)


def test_page_summary_readiness_does_not_wait_for_storyline():
    repository = FakeRecordRepository()
    application = ContentInsightsApplication(repository)
    run, _ = application.create_run(request(), "project-1", ArtifactType.PAGE_SUMMARY)
    page_id = artifact_id(run, "PAGE_SUMMARY")
    assert artifact_id(run, "STORYLINE") is None

    complete(application, page_id, "가벼운 본체와 관리 가능한 필터를 갖춘 무선 청소기")
    final = application.get_run(run["run_id"], "project-1", ArtifactType.PAGE_SUMMARY)
    assert final["status"] == "SUCCEEDED"
    assert final["required_artifacts_ready"] is True
    output = final["artifacts"]["PAGE_SUMMARY"]["output"]
    assert set(output) == {"schema_version", "content", "sections", "source_fields"}
    assert output["schema_version"] == 2 and output["content"] is None
    assert [section["role"] for section in output["sections"]] == [
        "WHAT",
        "WHY",
    ]


def test_storyline_runs_independently_without_staling_page_summary():
    repository = FakeRecordRepository()
    application = ContentInsightsApplication(repository)
    page, _ = application.create_run(request(), "project-1", ArtifactType.PAGE_SUMMARY)
    complete(application, artifact_id(page, "PAGE_SUMMARY"), "요약")
    storyline, dispatch = application.create_run(
        request(trigger="STORY_CONFIRMED"), "project-1", ArtifactType.STORYLINE
    )
    assert dispatch[0][1] == ArtifactType.STORYLINE
    assert application.get_run(page["run_id"], "project-1", ArtifactType.PAGE_SUMMARY)["status"] == "SUCCEEDED"
    with pytest.raises(LookupError):
        application.get_run(storyline["run_id"], "project-1", ArtifactType.PAGE_SUMMARY)
    storyline_id = artifact_id(storyline, "STORYLINE")
    with application.claim_artifact(storyline_id) as claimed:
        assert claimed is not None
        application.fail_artifact(storyline_id, ConnectionError("model failed"), prompt_version="test-v1")
    retried, rerun = application.retry_artifact(storyline["run_id"], ArtifactType.STORYLINE, "project-1")
    assert retried["artifacts"]["STORYLINE"]["status"] == "QUEUED"
    assert rerun == (storyline_id, ArtifactType.STORYLINE)
    complete_storyline(application, storyline_id)
    final = application.get_run(storyline["run_id"], "project-1", ArtifactType.STORYLINE)
    assert final["required_artifacts_ready"] is True
    assert final["artifacts"]["STORYLINE"]["attempts"] == 2
    output = final["artifacts"]["STORYLINE"]["output"]
    assert set(output) == {"schema_version", "content", "sections", "source_fields"}
    assert output["schema_version"] == 1 and output["sections"] is None
    assert output["content"].startswith("약 1.3kg")


def test_non_retryable_artifact_failure_requires_a_new_revision():
    repository = FakeRecordRepository()
    application = ContentInsightsApplication(repository)
    run, _ = application.create_run(
        request(trigger="STORY_CONFIRMED"), "project-1", ArtifactType.STORYLINE
    )
    storyline_id = artifact_id(run, "STORYLINE")

    with application.claim_artifact(storyline_id) as claimed:
        assert claimed is not None
        application.fail_artifact(storyline_id, ValueError("invalid output"), prompt_version="test-v1")

    with pytest.raises(ApplicationConflict, match="재시도할 수 없는"):
        application.retry_artifact(run["run_id"], ArtifactType.STORYLINE, "project-1")


def test_new_source_revision_marks_previous_artifacts_stale():
    repository = FakeRecordRepository()
    application = ContentInsightsApplication(repository)
    first, _ = application.create_run(request(), "project-1", ArtifactType.PAGE_SUMMARY)
    complete(
        application,
        artifact_id(first, "PAGE_SUMMARY"),
        "첫 프로젝트 revision을 바탕으로 생성한 상세 페이지 요약",
    )

    second, _ = application.create_run(
        request(revision=2, key="content-2"), "project-1", ArtifactType.PAGE_SUMMARY
    )
    stale = application.get_run(first["run_id"], "project-1", ArtifactType.PAGE_SUMMARY)
    assert second["source_revision"] == 2
    assert stale["status"] == "STALE"
    assert all(
        value["status"] == "STALE"
        for value in stale["artifacts"].values()
        if value["artifact_id"] is not None
    )
    with pytest.raises(ApplicationConflict, match="최신 프로젝트"):
        application.retry_artifact(first["run_id"], ArtifactType.PAGE_SUMMARY, "project-1")


def test_story_confirmed_policy_can_run_storyline_without_page_summary():
    repository = FakeRecordRepository()
    application = ContentInsightsApplication(repository)
    run, dispatch = application.create_run(
        request(trigger="STORY_CONFIRMED"),
        "project-1",
        ArtifactType.STORYLINE,
    )

    assert run["artifacts"]["PAGE_SUMMARY"]["status"] == "NOT_REQUESTED"
    assert run["artifacts"]["STORYLINE"]["required"] is True
    assert dispatch[0][1] == ArtifactType.STORYLINE


def test_registration_cannot_start_a_storyline_model_call():
    repository = FakeRecordRepository()
    application = ContentInsightsApplication(repository)

    with pytest.raises(ApplicationInvalid, match="trigger"):
        application.create_run(request(), "project-1", ArtifactType.STORYLINE)
    with pytest.raises(ApplicationInvalid, match="trigger"):
        application.create_run(
            request(trigger="STORY_CONFIRMED"), "project-1", ArtifactType.PAGE_SUMMARY
        )
    assert application.pending_artifacts() == []


def test_worker_uses_registered_generator_and_persists_output():
    repository = FakeRecordRepository()
    application = ContentInsightsApplication(repository)
    run, _ = application.create_run(
        request(),
        "project-1",
        ArtifactType.PAGE_SUMMARY,
    )
    page_id = artifact_id(run, "PAGE_SUMMARY")

    class Generator:
        artifact_type = ArtifactType.PAGE_SUMMARY
        prompt_version = "page-summary-test"

        def generate(self, aid, snapshot):
            assert aid == page_id and snapshot.title == "LUMI S1"
            return ArtifactOutput(
                schema_version=2,
                sections=[
                    {
                        "role": "WHAT",
                        "headline": "무선 청소기 구성",
                        "description": "약 1.3kg 본체와 세척 가능한 필터",
                    },
                    {
                        "role": "WHY",
                        "headline": "좁은 공간의 일상 청소",
                        "description": "좁은 공간에서 자주 청소하는 사용자를 위한 제품",
                    },
                ],
                source_fields=["description"],
            )

    execute_artifact(application, page_id, {ArtifactType.PAGE_SUMMARY: Generator()})
    result = application.get_run(run["run_id"], "project-1", ArtifactType.PAGE_SUMMARY)
    assert result["artifacts"]["PAGE_SUMMARY"]["status"] == "SUCCEEDED"
    assert result["artifacts"]["PAGE_SUMMARY"]["prompt_version"] == "page-summary-test"


def test_duplicate_delivery_does_not_invoke_generator_during_active_lease():
    repository = FakeRecordRepository()
    application = ContentInsightsApplication(repository)
    run, _ = application.create_run(request(), "project-1", ArtifactType.PAGE_SUMMARY)
    page_id = artifact_id(run, "PAGE_SUMMARY")
    repository.records[page_id]["data"].update(
        status="RUNNING",
        _lease_token="other-worker",
        _lease_until=time.time() + 60,
    )

    class Generator:
        artifact_type = ArtifactType.PAGE_SUMMARY
        prompt_version = "page-summary-test"

        def generate(self, aid, snapshot):
            raise AssertionError("잠긴 artifact의 generator가 실행되면 안 됩니다.")

    execute_artifact(application, page_id, {ArtifactType.PAGE_SUMMARY: Generator()})
    assert (
        application.get_run(run["run_id"], "project-1", ArtifactType.PAGE_SUMMARY)["artifacts"]["PAGE_SUMMARY"]["status"]
        == "RUNNING"
    )
