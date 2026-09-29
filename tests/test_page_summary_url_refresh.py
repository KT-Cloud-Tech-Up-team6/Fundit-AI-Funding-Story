from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Barrier
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from test_application import FakeRecordRepository
from test_content_insights import artifact_id, complete, request

from funding_story import media
from funding_story.api import app
from funding_story.application import ApplicationConflict
from funding_story.config import settings
from funding_story.content_insights import api, generators
from funding_story.content_insights.models import ArtifactType, ContentInsightCreateRequest, PageSummaryDraft
from funding_story.content_insights.service import ContentInsightsApplication
from funding_story.content_insights.worker import execute_artifact
from funding_story.infrastructure.persistence import connection, repository

PAGE = ArtifactType.PAGE_SUMMARY
PREFIX = "/api/v1/ai/page-summary-runs"


def signed_request(key="first", *, renewed=False, revision=1):
    body = request(revision=revision, key=key).model_dump(mode="json")
    body["project_snapshot"]["story_content"].append({
        "type": "IMAGE",
        "value": "https://files.example.com/body.png",
        "read_url": "https://files.example.com/body.png?signature=" + ("new" if renewed else "old"),
        "content_type": "image/png",
        "file_size": len(image_bytes()),
        "expires_at": "2099-01-01T00:00:00Z" if renewed else "2000-01-01T00:00:00Z",
    })
    return ContentInsightCreateRequest.model_validate(body)


def image_bytes():
    image = BytesIO()
    Image.new("RGB", (1, 1), "red").save(image, format="PNG")
    return image.getvalue()


def failed_expired_run(application, project):
    run, _ = application.create_run(signed_request(), project, PAGE)
    execute_artifact(application, artifact_id(run, "PAGE_SUMMARY"))
    result = application.get_run(run["run_id"], project, PAGE)
    assert result["status"] == "FAILED"
    assert result["artifacts"]["PAGE_SUMMARY"]["error"]["code"] == "IMAGE_READ_URL_EXPIRED"
    assert result["artifacts"]["PAGE_SUMMARY"]["error"]["retryable"] is False
    return result


def test_same_revision_refresh_replaces_failed_run_and_preserves_idempotency():
    records = FakeRecordRepository()
    application = ContentInsightsApplication(records)
    old = failed_expired_run(application, "project-1")
    # Existing records may have hashes that included expiring access information.
    records.records[old["run_id"]]["data"]["source_hash"] = "legacy-hash"
    renewed = signed_request("refreshed", renewed=True)
    new, dispatch = application.create_run(renewed, "project-1", PAGE)
    duplicate, repeated_dispatch = application.create_run(renewed, "project-1", PAGE)

    assert new["run_id"] != old["run_id"] and new["source_revision"] == old["source_revision"]
    assert len(dispatch) == 1 and repeated_dispatch == []
    assert duplicate["run_id"] == new["run_id"]
    assert application.get_run(old["run_id"], "project-1", PAGE)["status"] == "STALE"
    assert records.get(artifact_id(old, "PAGE_SUMMARY"))["data"]["status"] == "STALE"
    old_replay, old_dispatch = application.create_run(signed_request(), "project-1", PAGE)
    assert old_replay["status"] == "STALE" and old_dispatch == []
    with pytest.raises(ApplicationConflict):
        application.retry_artifact(old["run_id"], PAGE, "project-1")
    with pytest.raises(ApplicationConflict):
        application.create_run(signed_request("another", renewed=True), "project-1", PAGE)
    with pytest.raises(ApplicationConflict, match="중복 키"):
        application.create_run(signed_request("first", renewed=True), "project-1", PAGE)


@pytest.mark.parametrize("change", [
    "title", "category", "description", "reward", "text", "image", "size", "mime", "order", "trigger",
])
def test_url_refresh_rejects_changed_content(change):
    application = ContentInsightsApplication(FakeRecordRepository())
    old = failed_expired_run(application, "project-1")
    body = signed_request("refreshed", renewed=True).model_dump(mode="json")
    snapshot = body["project_snapshot"]
    if change in ("title", "category", "description"):
        snapshot[change] = "변경된 콘텐츠"
    elif change == "reward":
        snapshot["rewards"][0]["price"] += 100
    elif change == "text":
        snapshot["story_content"][0]["value"] = "수정한 본문"
    elif change == "image":
        snapshot["story_content"][1]["value"] = "https://files.example.com/other.png"
    elif change == "size":
        snapshot["story_content"][1]["file_size"] += 1
    elif change == "mime":
        snapshot["story_content"][1]["content_type"] = "image/jpeg"
    elif change == "order":
        snapshot["story_content"].reverse()
    else:
        body["trigger"] = "PROJECT_CONTENT_UPDATED"
    with pytest.raises(ApplicationConflict, match="콘텐츠"):
        application.create_run(ContentInsightCreateRequest.model_validate(body), "project-1", PAGE)
    assert application.get_run(old["run_id"], "project-1", PAGE)["status"] == "FAILED"


@pytest.mark.parametrize("change", ["unchanged_url", "expired_url", "removed_url"])
def test_url_refresh_requires_a_renewed_usable_reference(change):
    application = ContentInsightsApplication(FakeRecordRepository())
    failed_expired_run(application, "project-1")
    body = signed_request("refreshed", renewed=True).model_dump(mode="json")
    image = body["project_snapshot"]["story_content"][1]
    if change == "unchanged_url":
        image["read_url"] = str(signed_request().project_snapshot.story_content[1].read_url)
    elif change == "expired_url":
        image["expires_at"] = "2000-01-01T00:00:00Z"
    else:
        for field in ("read_url", "content_type", "file_size", "expires_at"):
            image.pop(field)
    with pytest.raises(ApplicationConflict):
        application.create_run(ContentInsightCreateRequest.model_validate(body), "project-1", PAGE)


@pytest.mark.parametrize("state", ["queued", "running", "succeeded", "other_failure", "transient_failure"])
def test_same_revision_exception_is_limited_to_expired_url_failure(state):
    application = ContentInsightsApplication(FakeRecordRepository())
    run, _ = application.create_run(signed_request(), "project-1", PAGE)
    aid = artifact_id(run, "PAGE_SUMMARY")
    if state == "succeeded":
        complete(application, aid, "검증된 상세 설명")
    elif state in ("other_failure", "transient_failure"):
        with application.claim_artifact(aid):
            error = ValueError("invalid output") if state == "other_failure" else ConnectionError("timeout")
            application.fail_artifact(aid, error)
    elif state == "running":
        with application.claim_artifact(aid), pytest.raises(ApplicationConflict):
            application.create_run(signed_request("new", renewed=True), "project-1", PAGE)
        return
    with pytest.raises(ApplicationConflict):
        application.create_run(signed_request("new", renewed=True), "project-1", PAGE)


def test_refresh_cannot_replace_a_newer_content_revision():
    application = ContentInsightsApplication(FakeRecordRepository())
    failed_expired_run(application, "project-1")
    application.create_run(signed_request("version-2", renewed=True, revision=2), "project-1", PAGE)
    with pytest.raises(ApplicationConflict, match="더 최신"):
        application.create_run(signed_request("refreshed", renewed=True), "project-1", PAGE)


def test_only_the_failed_image_must_receive_a_new_url():
    application = ContentInsightsApplication(FakeRecordRepository())
    initial = signed_request().model_dump(mode="json")
    initial["project_snapshot"]["story_content"].append(
        signed_request(renewed=True).project_snapshot.story_content[1].model_dump(mode="json")
    )
    old, _ = application.create_run(ContentInsightCreateRequest.model_validate(initial), "project-1", PAGE)
    execute_artifact(application, artifact_id(old, "PAGE_SUMMARY"))
    initial["idempotency_key"] = "refreshed"
    # Refreshing an unrelated image cannot recover the actual failed reference.
    initial["project_snapshot"]["story_content"][1]["expires_at"] = "2099-01-01T00:00:00Z"
    initial["project_snapshot"]["story_content"][2]["read_url"] = "https://files.example.com/body.png?sig=other"
    with pytest.raises(ApplicationConflict, match="만료로 실패한"):
        application.create_run(ContentInsightCreateRequest.model_validate(initial), "project-1", PAGE)
    initial["project_snapshot"]["story_content"][1]["read_url"] = "https://files.example.com/body.png?sig=renewed"
    new, _ = application.create_run(ContentInsightCreateRequest.model_validate(initial), "project-1", PAGE)
    assert new["status"] == "QUEUED"


def test_postgres_http_expiry_refresh_and_completion(postgres_container, monkeypatch):
    application = ContentInsightsApplication(repository())
    monkeypatch.setattr(api, "content_insights_application", application)
    headers = {"Authorization": "Bearer " + settings().ai_service_token, "X-Project-Id": str(uuid4())}

    def generate(rid, prompt, model, **kwargs):
        assert kwargs["references"] == [(image_bytes(), "image/png")]
        return PageSummaryDraft(sections=[
            {"role": "WHAT", "headline": "프로젝트 구성", "description": "입력 이미지와 본문을 확인한 구성"},
            {"role": "WHY", "headline": "프로젝트 필요성", "description": "입력에 근거한 사용 맥락"},
        ])

    monkeypatch.setattr(generators, "generate_checked", generate)
    with TestClient(app, headers=headers) as client:
        created = client.post(PREFIX, json=signed_request().model_dump(mode="json"))
        assert created.status_code == 202
        old = created.json()
        execute_artifact(application, artifact_id(old, "PAGE_SUMMARY"))
        failed = client.get(PREFIX + "/" + old["run_id"]).json()
        assert failed["status"] == "FAILED"
        error = failed["artifacts"]["PAGE_SUMMARY"]["error"]
        assert error["code"] == "IMAGE_READ_URL_EXPIRED" and error["retryable"] is False
        assert "signature" not in str(error)
        assert client.post(PREFIX + "/" + old["run_id"] + "/retry").status_code == 409
        same_body = signed_request("refreshed", renewed=True).model_dump(mode="json")
        accepted = client.post(PREFIX, json=same_body)
        assert accepted.status_code == 202
        new = accepted.json()
        assert client.post(PREFIX, json=same_body).json()["run_id"] == new["run_id"]
        assert client.get(PREFIX + "/" + old["run_id"]).json()["status"] == "STALE"
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, headers={"Content-Type": "image/png"}, content=image_bytes()
        ))) as image_client:
            monkeypatch.setattr(media.httpx, "stream", image_client.stream)
            execute_artifact(application, artifact_id(new, "PAGE_SUMMARY"))
        result = client.get(PREFIX + "/" + new["run_id"]).json()
        assert result["status"] == "SUCCEEDED" and result["required_artifacts_ready"] is True
        assert result["source_revision"] == old["source_revision"]
        sections = result["artifacts"]["PAGE_SUMMARY"]["output"]["sections"]
        assert all(section["headline"] and section["description"] for section in sections)


@pytest.mark.parametrize("same_key", [True, False])
def test_postgres_concurrent_refresh_creates_only_one_run(postgres_container, same_key):
    records = repository()
    application = ContentInsightsApplication(records)
    project = str(uuid4())
    old = failed_expired_run(application, project)
    barrier = Barrier(2)

    def submit(index):
        barrier.wait(timeout=5)
        try:
            return application.create_run(signed_request(
                "refresh" if same_key else f"refresh-{index}", renewed=True
            ), project, PAGE)
        except ApplicationConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    accepted = [result for result in results if result is not None]
    assert len(accepted) == (2 if same_key else 1)
    assert sum(len(dispatch) for _, dispatch in accepted) == 1
    assert len({run["run_id"] for run, _ in accepted}) == 1
    with connection() as conn:
        count = conn.execute(
            "SELECT count(*) AS n FROM ai_records WHERE project_id=%s AND kind='page_summary_run'",
            (project,),
        ).fetchone()["n"]
        assert count == 2
        # Even a late timestamp on the old run must not select it again.
        conn.execute("UPDATE ai_records SET updated_at=clock_timestamp() + interval '1 second' WHERE id=%s",
                     (old["run_id"],))
    assert records.latest(project, "page_summary_run")["id"] == accepted[0][0]["run_id"]
    with pytest.raises(ApplicationConflict):
        application.create_run(signed_request("third", renewed=True), project, PAGE)
