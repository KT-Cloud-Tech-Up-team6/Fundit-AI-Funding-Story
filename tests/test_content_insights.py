import pytest
from test_application import FakeRecordRepository

from funding_story.application import ApplicationConflict
from funding_story.content_insights import (
    ArtifactOutput,
    ArtifactType,
    ContentInsightCreateRequest,
    ContentInsightRunResponse,
    ContentInsightsApplication,
)
from funding_story.content_insights.worker import execute_artifact


def request(revision=1, key="content-1", artifacts=None, trigger="PROJECT_REGISTRATION_COMPLETED"):
    return ContentInsightCreateRequest(
        source_revision=revision,
        idempotency_key=key,
        trigger=trigger,
        requested_artifacts=artifacts or [],
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
            ArtifactOutput(content=text, source_fields=["description"]),
            prompt_version="test-v1",
            model="test-model",
        )


def complete_storyline(application, aid):
    with application.claim_artifact(aid) as claimed:
        assert claimed is not None
        application.complete_artifact(
            aid,
            ArtifactOutput(
                schema_version=2,
                sections=[
                    {
                        "role": "REWARD_IDENTITY",
                        "headline": "가볍게 꺼내 쓰는 무선 청소기",
                        "description": "약 1.3kg 본체와 틈새 노즐을 포함한 구성",
                    },
                    {
                        "role": "PROJECT_REASON",
                        "headline": "좁은 공간의 청소 부담 완화",
                        "description": "큰 청소기를 꺼내기 번거로운 상황을 위한 선택지",
                    },
                ],
                source_fields=["description", "story_content", "rewards"],
            ),
            prompt_version="storyline-v2",
            model="test-model",
        )


def test_content_insight_run_is_idempotent_and_artifacts_are_independent():
    repository = FakeRecordRepository()
    application = ContentInsightsApplication(repository)

    run, dispatch = application.create_run(request(), "project-1")
    duplicate, duplicate_dispatch = application.create_run(request(), "project-1")

    parsed = ContentInsightRunResponse.model_validate(run)
    assert parsed.status.value == "QUEUED"
    assert parsed.artifacts[ArtifactType.PAGE_SUMMARY].required is True
    assert parsed.artifacts[ArtifactType.STORYLINE].required is True
    assert {artifact_type for _, artifact_type in dispatch} == set(ArtifactType)
    assert duplicate["run_id"] == run["run_id"] and duplicate_dispatch == []
    assert set(application.pending_job_ids()) == {artifact_id for artifact_id, _ in dispatch}

    changed = request()
    changed.project_snapshot.description = "다른 설명"
    with pytest.raises(ApplicationConflict, match="중복 키"):
        application.create_run(changed, "project-1")


def test_required_readiness_waits_for_page_summary_and_storyline():
    repository = FakeRecordRepository()
    application = ContentInsightsApplication(repository)
    run, _ = application.create_run(request(), "project-1")
    page_id = artifact_id(run, "PAGE_SUMMARY")
    storyline_id = artifact_id(run, "STORYLINE")

    complete(application, page_id, "가벼운 본체와 관리 가능한 필터를 갖춘 무선 청소기입니다.")
    after_page = application.get_run(run["run_id"], "project-1")
    assert after_page["status"] == "RUNNING"
    assert after_page["required_artifacts_ready"] is False

    with application.claim_artifact(storyline_id) as claimed:
        assert claimed is not None
        application.fail_artifact(storyline_id, ConnectionError("model failed"), prompt_version="test-v1")
    partial = application.get_run(run["run_id"], "project-1")
    assert partial["status"] == "PARTIALLY_SUCCEEDED"
    assert partial["required_artifacts_ready"] is False
    assert partial["artifacts"]["STORYLINE"]["error"]["code"] == "CONNECTIONERROR"

    retried, dispatch = application.retry_artifact(run["run_id"], ArtifactType.STORYLINE, "project-1")
    assert retried["artifacts"]["STORYLINE"]["status"] == "QUEUED"
    assert dispatch == (storyline_id, ArtifactType.STORYLINE)
    complete_storyline(application, storyline_id)
    final = application.get_run(run["run_id"], "project-1")
    assert final["status"] == "SUCCEEDED"
    assert final["required_artifacts_ready"] is True
    assert final["artifacts"]["STORYLINE"]["attempts"] == 2
    assert final["artifacts"]["STORYLINE"]["output"]["sections"][0]["role"] == "REWARD_IDENTITY"


def test_non_retryable_artifact_failure_requires_a_new_revision():
    repository = FakeRecordRepository()
    application = ContentInsightsApplication(repository)
    run, _ = application.create_run(request(), "project-1")
    storyline_id = artifact_id(run, "STORYLINE")

    with application.claim_artifact(storyline_id) as claimed:
        assert claimed is not None
        application.fail_artifact(storyline_id, ValueError("invalid output"), prompt_version="test-v1")

    with pytest.raises(ApplicationConflict, match="재시도할 수 없는"):
        application.retry_artifact(run["run_id"], ArtifactType.STORYLINE, "project-1")


def test_new_source_revision_marks_previous_artifacts_stale():
    repository = FakeRecordRepository()
    application = ContentInsightsApplication(repository)
    first, _ = application.create_run(request(), "project-1")
    complete(
        application,
        artifact_id(first, "PAGE_SUMMARY"),
        "첫 프로젝트 revision을 바탕으로 생성한 상세 페이지 요약입니다.",
    )

    second, _ = application.create_run(request(revision=2, key="content-2"), "project-1")
    stale = application.get_run(first["run_id"], "project-1")
    assert second["source_revision"] == 2
    assert stale["status"] == "STALE"
    assert all(
        value["status"] == "STALE"
        for value in stale["artifacts"].values()
        if value["artifact_id"] is not None
    )
    with pytest.raises(ApplicationConflict, match="최신 프로젝트"):
        application.retry_artifact(first["run_id"], ArtifactType.STORYLINE, "project-1")


def test_story_confirmed_policy_can_run_storyline_without_page_summary():
    repository = FakeRecordRepository()
    application = ContentInsightsApplication(repository)
    run, dispatch = application.create_run(
        request(trigger="STORY_CONFIRMED", artifacts=["STORYLINE"]),
        "project-1",
    )

    assert run["artifacts"]["PAGE_SUMMARY"]["status"] == "NOT_REQUESTED"
    assert run["artifacts"]["STORYLINE"]["required"] is True
    assert dispatch[0][1] == ArtifactType.STORYLINE


def test_worker_uses_registered_generator_and_persists_output():
    repository = FakeRecordRepository()
    application = ContentInsightsApplication(repository)
    run, _ = application.create_run(
        request(artifacts=["PAGE_SUMMARY"]),
        "project-1",
    )
    page_id = artifact_id(run, "PAGE_SUMMARY")

    class Generator:
        artifact_type = ArtifactType.PAGE_SUMMARY
        prompt_version = "page-summary-test"

        def generate(self, aid, snapshot):
            assert aid == page_id and snapshot.title == "LUMI S1"
            return ArtifactOutput(
                content="약 1.3kg 본체와 세척 가능한 필터를 갖춘 무선 청소기입니다.",
                source_fields=["description"],
            )

    execute_artifact(application, page_id, {ArtifactType.PAGE_SUMMARY: Generator()})
    result = application.get_run(run["run_id"], "project-1")
    assert result["artifacts"]["PAGE_SUMMARY"]["status"] == "SUCCEEDED"
    assert result["artifacts"]["PAGE_SUMMARY"]["prompt_version"] == "page-summary-test"


def test_duplicate_delivery_does_not_invoke_generator_when_lock_is_unavailable():
    class LockedRepository(FakeRecordRepository):
        def try_job_lock(self, value):
            return False

    repository = LockedRepository()
    application = ContentInsightsApplication(repository)
    run, _ = application.create_run(request(artifacts=["PAGE_SUMMARY"]), "project-1")
    page_id = artifact_id(run, "PAGE_SUMMARY")

    class Generator:
        artifact_type = ArtifactType.PAGE_SUMMARY
        prompt_version = "page-summary-test"

        def generate(self, aid, snapshot):
            raise AssertionError("잠긴 artifact의 generator가 실행되면 안 됩니다.")

    execute_artifact(application, page_id, {ArtifactType.PAGE_SUMMARY: Generator()})
    assert application.get_run(run["run_id"], "project-1")["artifacts"]["PAGE_SUMMARY"]["status"] == "QUEUED"
