import json
from io import BytesIO
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from test_application import FakeRecordRepository, complete_review, confirmed_session, context
from test_content_insights import artifact_id, request
from test_recovery import FakeBackend

from funding_story import api, media, tasks
from funding_story.application import FundingStoryApplication
from funding_story.content_insights import (
    ArtifactType,
    ContentInsightCreateRequest,
    ContentInsightsApplication,
)
from funding_story.content_insights.models import ContentInsightRunResponse
from funding_story.content_insights.worker import execute_artifact
from funding_story.input_image_errors import InputImageValidationError
from funding_story.models import MessageRequest, Review, RunRequest, SessionCreateRequest, SourceImageRef


def image_bytes(format="JPEG"):
    output = BytesIO()
    Image.new("RGB", (2, 2), "red").save(output, format=format)
    return output.getvalue()


def source_image(content, mime):
    return SourceImageRef(
        slot_id="project.cover",
        read_url="https://storage.example.test/image.png?signature=never-expose",
        content_type=mime,
        file_size=len(content),
        expires_at="2099-01-01T00:00:00Z",
    )


@pytest.fixture
def storage_response(monkeypatch):
    clients = []

    def respond(content, mime):
        client = httpx.Client(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, headers={"Content-Type": mime}, content=content)
        ))
        clients.append(client)
        monkeypatch.setattr(media.httpx, "stream", client.stream)

    yield respond
    for client in clients:
        client.close()


@pytest.mark.parametrize("format,mime", [
    ("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")
])
def test_valid_supported_images_are_not_converted(storage_response, format, mime):
    content = image_bytes(format)
    storage_response(content, mime)
    assert media.read_source_image(source_image(content, mime)) == (content, mime)


@pytest.mark.parametrize("content,mime,expected_code", [
    (image_bytes(), "image/png", "INPUT_IMAGE_TYPE_MISMATCH"),
    (image_bytes(), "image/gif", "INPUT_IMAGE_UNSUPPORTED_TYPE"),
    (b"not-an-image", "image/png", "INPUT_IMAGE_INVALID"),
])
def test_image_validation_errors_are_specific_and_safe(storage_response, content, mime, expected_code):
    storage_response(content, mime)
    with pytest.raises(InputImageValidationError) as error:
        media.read_public_image("https://storage.example.test/image.png?signature=never-expose")
    public = error.value.public_error()
    assert public["code"] == expected_code and public["retryable"] is False
    assert public["detail"] is None
    assert "signature" not in json.dumps(public) and "never-expose" not in json.dumps(public)


def test_source_mime_and_storage_header_mismatch_is_explicit(storage_response):
    content = image_bytes()
    storage_response(content, "image/jpeg")
    with pytest.raises(InputImageValidationError) as error:
        media.read_source_image(source_image(content, "image/png"))
    assert error.value.code == "INPUT_IMAGE_TYPE_MISMATCH"


def test_corrupt_png_verification_returns_invalid_image(storage_response):
    content = bytearray(image_bytes("PNG"))
    content[29] ^= 1  # Corrupt the IHDR CRC while retaining the PNG header.
    storage_response(bytes(content), "image/png")
    with pytest.raises(InputImageValidationError) as error:
        media.read_source_image(source_image(bytes(content), "image/png"))
    assert error.value.code == "INPUT_IMAGE_INVALID"


@pytest.mark.parametrize("mode", ["initial", "message"])
@pytest.mark.parametrize("mime", ["image/png", "image/jpeg"])
def test_chat_sse_distinguishes_mismatch_before_any_model_call(
    monkeypatch, storage_response, capsys, mode, mime
):
    app = FundingStoryApplication(FakeRecordRepository())
    monkeypatch.setattr(api, "application", app)
    monkeypatch.setattr(tasks, "application", app)
    monkeypatch.setattr(api, "open_pools", lambda: None)
    monkeypatch.setattr(api, "close_pools", lambda: None)
    monkeypatch.setattr(api, "kick", lambda _: None)
    content = image_bytes()
    storage_response(content, mime)
    body = context()
    body["source_images"] = [source_image(content, mime).model_dump(mode="json")]
    model_calls = []

    def generate(*args, **kwargs):
        model_calls.append(True)
        return Review.model_validate(complete_review())

    monkeypatch.setattr(tasks, "generate_checked", generate)
    monkeypatch.setattr(tasks.provider, "chat_stream", lambda _: iter(["확인했습니다."]))
    project = str(uuid4())
    session = app.create_session(project, SessionCreateRequest(context=body))
    if mode == "initial":
        accepted, _ = app.start_session(session["session_id"], project)
    else:
        accepted, _ = app.add_message(
            session["session_id"], MessageRequest(message_id="one", revision=1, text="제품 정보"), project
        )
    tasks.execute(accepted["chat_id"])
    with TestClient(api.app, headers={"Authorization": "Bearer test-only", "X-Project-Id": project}) as client:
        response = client.get(f"/api/v1/ai/chats/{accepted['chat_id']}/events")
    done_data = response.text.split("event: done\ndata: ")[1].strip()
    done = json.loads(done_data)
    current = app.get_session(session["session_id"], project)
    assert current["active_chat_id"] is None
    if mime == "image/png":
        assert done["status"] == "failed" and model_calls == []
        assert done["error"] == InputImageValidationError("INPUT_IMAGE_TYPE_MISMATCH").public_error()
        assert "INPUT_IMAGE_TYPE_MISMATCH" in capsys.readouterr().out
        assert current["revision"] == 1
    else:
        assert done["status"] == "succeeded" and done["error"] is None
        assert len(model_calls) == 1 and current["revision"] == 2
    assert "signature" not in response.text


def test_full_generation_reports_input_error_to_backend_before_copy_model(
    monkeypatch, storage_response, capsys
):
    app = FundingStoryApplication(FakeRecordRepository())
    monkeypatch.setattr(tasks, "application", app)
    backend = FakeBackend()
    monkeypatch.setattr(tasks, "BackendClient", lambda _: backend)
    session_id = confirmed_session(app)
    content = image_bytes()
    storage_response(content, "image/png")
    body = context()
    body["source_images"] = [source_image(content, "image/png").model_dump(mode="json")]
    run, _ = app.create_run(
        RunRequest(session_id=session_id, confirmed_revision=2, idempotency_key="run", context=body),
        "project-1",
    )

    def unexpected_model(*args, **kwargs):
        pytest.fail("An invalid input image must not invoke a model")

    monkeypatch.setattr(tasks, "generate_checked", unexpected_model)
    tasks.execute(run["run_id"])
    assert backend.completion.status == "failed"
    assert backend.completion.error.code == "INPUT_IMAGE_TYPE_MISMATCH"
    assert backend.completion.error.retryable is False
    assert app.get_session(session_id, "project-1")["active_chat_id"] is None
    assert "signature" not in backend.completion.model_dump_json()
    assert "never-expose" not in capsys.readouterr().out


def test_page_summary_reports_input_error_without_allowing_same_input_retry(
    monkeypatch, storage_response, capsys
):
    app = ContentInsightsApplication(FakeRecordRepository())
    content = image_bytes()
    storage_response(content, "image/png")
    body = request().model_dump(mode="json")
    reference = source_image(content, "image/png")
    image = reference.model_dump(mode="json")
    body["project_snapshot"]["story_content"].append({
        "type": "IMAGE",
        "value": "https://cdn.example.test/image.png",
        **{key: value for key, value in image.items() if key not in ("slot_id", "reward_id")},
    })
    run, _ = app.create_run(ContentInsightCreateRequest.model_validate(body), "project-1", ArtifactType.PAGE_SUMMARY)

    def unexpected_model(*args, **kwargs):
        pytest.fail("An invalid input image must not invoke a model")

    from funding_story.content_insights import generators
    monkeypatch.setattr(generators, "generate_checked", unexpected_model)
    execute_artifact(app, artifact_id(run, "PAGE_SUMMARY"))
    result = app.get_run(run["run_id"], "project-1", ArtifactType.PAGE_SUMMARY)
    parsed = ContentInsightRunResponse.model_validate(result)
    error = parsed.artifacts[ArtifactType.PAGE_SUMMARY].error
    assert error.code == "INPUT_IMAGE_TYPE_MISMATCH" and error.retryable is False
    assert "INPUT_IMAGE_TYPE_MISMATCH" in capsys.readouterr().out
    assert "signature" not in error.model_dump_json()


def test_unrelated_chat_errors_keep_the_existing_generic_response():
    app = FundingStoryApplication(FakeRecordRepository())
    session = app.create_session("project-1", SessionCreateRequest(context=context()))
    accepted, _ = app.start_session(session["session_id"], "project-1")
    app.fail_job(accepted["chat_id"], RuntimeError("sensitive-provider-response"))
    error = app.get_chat(accepted["chat_id"], "project-1")["error"]
    assert error["code"] == "CHAT_FAILED" and error["retryable"] is True
    assert error["message"] == "답변 생성에 실패했습니다."
    assert "sensitive-provider-response" not in json.dumps(error)
