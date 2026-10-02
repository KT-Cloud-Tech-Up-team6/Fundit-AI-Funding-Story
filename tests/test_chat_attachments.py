import base64
import copy
import json
from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from test_application import FakeRecordRepository, complete_review, context
from test_recovery import FakeBackend, scene

from funding_story import api, media, provider, tasks
from funding_story.application import ApplicationConflict, ApplicationInvalid, FundingStoryApplication
from funding_story.infrastructure.persistence import repository
from funding_story.infrastructure.ttl_state import PostgresTtlRecordRepository
from funding_story.models import (
    ConfirmRequest,
    CopyResult,
    MessageRequest,
    Review,
    RunRequest,
    SessionCreateRequest,
)


def png(color="red"):
    output = BytesIO()
    Image.new("RGB", (2, 2), color).save(output, format="PNG")
    return output.getvalue()


def attachment(slot="chat.original", *, signature="first", reward_id=None, color="red"):
    return {
        "slot_id": slot,
        "file_url": f"https://cdn.example.test/media/projects/test/story/{slot}.png",
        "read_url": f"https://storage.example.test/{slot}.png?signature={signature}",
        "content_type": "image/png",
        "file_size": len(png(color)),
        "expires_at": "2099-01-01T00:00:00Z",
        "reward_id": reward_id,
    }


def source(image):
    return {key: value for key, value in image.items() if key != "file_url"}


def finish_chat(application, session_id, chat_id, project, revision):
    application.complete_chat(
        session_id=session_id, chat_id=chat_id, project=project, source_revision=revision,
        review=complete_review(), reply="이미지를 확인했습니다.",
    )


@pytest.fixture
def client(monkeypatch):
    records = FakeRecordRepository()
    application = FundingStoryApplication(records)
    monkeypatch.setattr(api, "application", application)
    monkeypatch.setattr(api, "open_pools", lambda: None)
    monkeypatch.setattr(api, "close_pools", lambda: None)
    monkeypatch.setattr(api, "kick", lambda _: None)
    with TestClient(api.app, headers={
        "Authorization": "Bearer test-only", "X-Project-Id": str(uuid4()),
    }) as test_client:
        yield test_client, application, records


def test_image_only_message_is_accepted_and_restored_without_signed_urls(client):
    http, application, _ = client
    session_id = http.post("/api/v1/ai/sessions", json={"context": context()}).json()["session_id"]
    image = attachment()
    accepted = http.post(f"/api/v1/ai/sessions/{session_id}/messages", json={
        "message_id": "image-only", "revision": 1, "attachments": [image],
    })
    assert accepted.status_code == 202
    result = http.get(f"/api/v1/ai/sessions/{session_id}").json()
    assert result["messages"][0]["text"] == ""
    metadata = result["messages"][0]["attachments"][0]
    assert metadata == {key: value for key, value in image.items() if key not in ("read_url", "expires_at")}
    assert "signature" not in json.dumps(result) and "read_url" not in json.dumps(result)
    assert http.get("/api/v1/ai/sessions/latest").json()["session"] == result
    finish_chat(application, session_id, accepted.json()["chat_id"], http.headers["X-Project-Id"], 1)
    restored = http.get(f"/api/v1/ai/sessions/{session_id}").json()
    assert "attachments" not in restored["messages"][-1]
    assert http.get(
        f"/api/v1/ai/sessions/{session_id}", headers={"X-Project-Id": str(uuid4())},
    ).status_code == 404


@pytest.mark.parametrize("body", [
    {"text": " "},
    {"attachments": [attachment(), attachment()]},
    {"attachments": [attachment(slot="project.cover")]},
    {"attachments": [{**attachment(), "file_url": "https://cdn.test/image?signature=secret"}]},
    {"attachments": [{**attachment(), "file_url": "https://user:secret@cdn.test/image.png"}]},
    {"attachments": [{**attachment(), "content_type": "image/gif"}]},
    {"attachments": [{**attachment(), "file_size": 10 * 1024 * 1024 + 1}]},
])
def test_invalid_attachment_requests_are_rejected_before_queuing(client, body):
    http, _, records = client
    session_id = http.post("/api/v1/ai/sessions", json={"context": context()}).json()["session_id"]
    response = http.post(f"/api/v1/ai/sessions/{session_id}/messages", json={
        "message_id": "invalid", "revision": 1, **body,
    })
    assert response.status_code == 400 and response.json()["code"] == "INVALID_INPUT"
    assert len(records.records) == 1
    assert "secret" not in response.text


@pytest.mark.parametrize("change", [
    {"reward_id": 999}, {"expires_at": "2000-01-01T00:00:00Z"},
])
def test_invalid_reward_or_expired_read_url_does_not_modify_session(client, change):
    http, _, records = client
    session_id = http.post("/api/v1/ai/sessions", json={"context": context()}).json()["session_id"]
    before = copy.deepcopy(records.records)
    response = http.post(f"/api/v1/ai/sessions/{session_id}/messages", json={
        "message_id": "invalid", "revision": 1, "attachments": [{**attachment(), **change}],
    })
    assert response.status_code == 422
    assert records.records == before


def test_attachment_total_limit_includes_existing_core_images(client):
    http, _, records = client
    core = context()
    core["source_images"] = [source(attachment(f"core-{i}")) for i in range(30)]
    session_id = http.post("/api/v1/ai/sessions", json={"context": core}).json()["session_id"]
    response = http.post(f"/api/v1/ai/sessions/{session_id}/messages", json={
        "message_id": "over-limit", "revision": 1, "attachments": [attachment()],
    })
    assert response.status_code == 422 and len(records.records) == 1


def test_message_retry_accepts_new_signature_but_rejects_changed_attachment(client):
    http, _, _ = client
    session_id = http.post("/api/v1/ai/sessions", json={"context": context()}).json()["session_id"]
    url = f"/api/v1/ai/sessions/{session_id}/messages"
    body = {"message_id": "once", "revision": 1, "text": "제품 참고", "attachments": [attachment()]}
    first = http.post(url, json=body)
    duplicate = http.post(url, json={**body, "attachments": [attachment(signature="renewed")]})
    assert first.status_code == 202 and duplicate.json() == first.json()
    for image in (
        {**attachment(), "read_url": "https://storage.example.test/different.png"},
        {**attachment(), "file_size": 1},
        {**attachment(), "file_url": "https://cdn.example.test/different.png"},
    ):
        assert http.post(url, json={**body, "attachments": [image]}).status_code == 409


def test_multiple_attachments_survive_chat_and_core_refresh_and_require_fresh_urls(client):
    http, application, records = client
    project = http.headers["X-Project-Id"]
    session_id = http.post("/api/v1/ai/sessions", json={"context": context()}).json()["session_id"]
    first = http.post(f"/api/v1/ai/sessions/{session_id}/messages", json={
        "message_id": "first", "revision": 1, "attachments": [attachment()],
    }).json()
    finish_chat(application, session_id, first["chat_id"], project, 1)
    refreshed = context()
    refreshed["source_images"] = [source(attachment(signature="chat-refresh"))]
    second = http.post(f"/api/v1/ai/sessions/{session_id}/messages", json={
        "message_id": "second", "revision": 2, "text": "다른 각도", "context": refreshed,
        "attachments": [attachment("chat.second", reward_id=1)],
    })
    assert second.status_code == 202
    finish_chat(application, session_id, second.json()["chat_id"], project, 2)
    application.confirm_session(session_id, ConfirmRequest(revision=3), project)
    request = {
        "session_id": session_id, "confirmed_revision": 3, "idempotency_key": "run", "context": context(),
    }
    before = copy.deepcopy(records.records)
    assert http.post("/api/v1/ai/runs", json=request).status_code == 422
    assert records.records == before
    fresh = [source(attachment(signature="run-refresh")), source(attachment("chat.second", reward_id=1))]
    for replacement in (
        fresh[:1],
        [{**fresh[0], "expires_at": "2000-01-01T00:00:00Z"}, fresh[1]],
        [{**fresh[0], "read_url": "https://storage.example.test/replaced.png"}, fresh[1]],
    ):
        status = http.post("/api/v1/ai/runs", json={
            **request, "context": {**context(), "source_images": replacement},
        }).status_code
        assert status in (409, 422)
    request["context"]["source_images"] = fresh
    accepted = http.post("/api/v1/ai/runs", json=request)
    assert accepted.status_code == 202
    final_context, _, _ = application.worker_context(accepted.json()["run_id"])
    assert [image.slot_id for image in final_context.source_images] == ["chat.original", "chat.second"]
    assert "run-refresh" in str(final_context.source_images[0].read_url)
    fresh[0]["read_url"] = "https://storage.example.test/chat.original.png?signature=retry"
    assert http.post("/api/v1/ai/runs", json=request).json() == accepted.json()


def test_text_only_legacy_messages_remain_compatible_and_block_attachments_during_active_run(client):
    http, application, records = client
    project = http.headers["X-Project-Id"]
    session_id = http.post("/api/v1/ai/sessions", json={"context": context()}).json()["session_id"]
    body = {"message_id": "legacy", "revision": 1, "text": "제품 설명"}
    url = f"/api/v1/ai/sessions/{session_id}/messages"
    accepted = http.post(url, json=body).json()
    data = records.get(session_id)["data"]
    del data["message_requests"]["legacy"]["fingerprint"]
    records.save(session_id, data)
    assert http.post(url, json=body).json() == accepted
    assert http.post(url, json={**body, "attachments": [attachment()]}).status_code == 409
    assert http.get(f"/api/v1/ai/sessions/{session_id}").json()["messages"] == [{
        "role": "user", "text": "제품 설명",
    }]
    finish_chat(application, session_id, accepted["chat_id"], project, 1)
    application.confirm_session(session_id, ConfirmRequest(revision=2), project)
    run = http.post("/api/v1/ai/runs", json={
        "session_id": session_id, "confirmed_revision": 2, "idempotency_key": "run", "context": context(),
    })
    assert run.status_code == 202
    assert http.post(url, json={
        "message_id": "late-image", "revision": 2, "attachments": [attachment()],
    }).status_code == 409


def test_chat_attachment_reaches_review_and_openai_image_edit(client, monkeypatch):
    http, application, _ = client
    monkeypatch.setattr(tasks, "application", application)
    core_png, attached_png = png("blue"), png("red")
    fetched = []

    def storage(request):
        fetched.append(str(request.url))
        content = attached_png if "chat.original" in request.url.path else core_png
        return httpx.Response(200, headers={"Content-Type": "image/png"}, content=content)

    with httpx.Client(transport=httpx.MockTransport(storage)) as storage_client:
        monkeypatch.setattr(media.httpx, "stream", storage_client.stream)
        core = context()
        core["source_images"] = [source(attachment("project.cover", color="blue"))]
        session_id = http.post("/api/v1/ai/sessions", json={"context": core}).json()["session_id"]
        chat = http.post(f"/api/v1/ai/sessions/{session_id}/messages", json={
            "message_id": "image", "revision": 1, "attachments": [attachment()],
        }).json()
        reviews = []

        def review(*args, **kwargs):
            reviews.append(kwargs["references"])
            return Review.model_validate(complete_review())

        monkeypatch.setattr(tasks, "generate_checked", review)
        monkeypatch.setattr(tasks.provider, "chat_stream", lambda _: iter(["확인"] ))
        tasks.execute(chat["chat_id"])
        assert reviews == [[(core_png, "image/png"), (attached_png, "image/png")]]
        assert http.post(f"/api/v1/ai/sessions/{session_id}/confirm", json={"revision": 2}).status_code == 200
        core["source_images"].append(source(attachment(signature="generation")))
        run = http.post("/api/v1/ai/runs", json={
            "session_id": session_id, "confirmed_revision": 2, "idempotency_key": "generate", "context": core,
        }).json()
        monkeypatch.setattr(tasks, "plan", lambda *_: (scene(), {}))
        monkeypatch.setattr(tasks, "generate_checked", lambda *args, **kwargs: CopyResult(
            texts={}, image_prompts={"hero.image": "hero", "benefit.image": "benefit"},
        ))
        edits = []

        def edit(**kwargs):
            edits.append([file.read() for file in kwargs["image"]])
            return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(attached_png).decode())])

        @contextmanager
        def openai_client():
            yield SimpleNamespace(images=SimpleNamespace(edit=edit))

        monkeypatch.setattr(provider, "openai_client", openai_client)
        monkeypatch.setattr(provider, "settings", lambda: SimpleNamespace(
            image_provider="openai", image_model="test-model", image_quality="medium",
        ))
        monkeypatch.setattr(tasks, "render_scene", lambda current, sources, block_ids: [{
            "block_id": next(iter(block_ids)), "bytes": attached_png, "width": 860, "height": 1200,
        }])
        backend = FakeBackend()
        monkeypatch.setattr(tasks, "BackendClient", lambda _: backend)
        tasks.execute(run["run_id"])
        assert edits == [[core_png, attached_png], [core_png, attached_png]]
        assert backend.completion.status == "succeeded"
        assert any("signature=generation" in url for url in fetched)


def test_attachments_persist_across_application_restart_without_new_schema(postgres_container):
    records = PostgresTtlRecordRepository(repository(), 300)
    application = FundingStoryApplication(records)
    project = str(uuid4())
    session_id = application.create_session(project, SessionCreateRequest(context=context()))["session_id"]
    accepted, _ = application.add_message(
        session_id, MessageRequest(message_id="image", revision=1, attachments=[attachment()]), project,
    )
    restarted = FundingStoryApplication(PostgresTtlRecordRepository(repository(), 300))
    restored = restarted.get_session(session_id, project)
    assert restored["messages"][0]["attachments"][0]["slot_id"] == "chat.original"
    finish_chat(restarted, session_id, accepted["chat_id"], project, 1)
    restarted.confirm_session(session_id, ConfirmRequest(revision=2), project)
    run, _ = restarted.create_run(RunRequest(
        session_id=session_id, confirmed_revision=2, idempotency_key="run",
        context={**context(), "source_images": [source(attachment(signature="renewed"))]},
    ), project)
    assert restarted.worker_context(run["run_id"])[0].source_images[0].slot_id == "chat.original"
    with pytest.raises(LookupError):
        restarted.get_session(session_id, str(uuid4()))


def test_refresh_cannot_silently_remove_or_replace_an_accepted_attachment(client):
    http, application, _ = client
    session_id = http.post("/api/v1/ai/sessions", json={"context": context()}).json()["session_id"]
    chat = http.post(f"/api/v1/ai/sessions/{session_id}/messages", json={
        "message_id": "first", "revision": 1, "attachments": [attachment()],
    }).json()
    finish_chat(application, session_id, chat["chat_id"], http.headers["X-Project-Id"], 1)
    for core, status in (
        (context(), 422),
        ({**context(), "source_images": [{**source(attachment()), "reward_id": 1}]}, 409),
        ({**context(), "source_images": [source(attachment()), source(attachment("chat.unaccepted"))]}, 422),
    ):
        response = http.post(f"/api/v1/ai/sessions/{session_id}/messages", json={
            "message_id": "second", "revision": 2, "text": "계속", "context": core,
        })
        assert response.status_code == status


def test_changed_attachment_requires_a_new_slot_id(client):
    http, application, _ = client
    session_id = http.post("/api/v1/ai/sessions", json={"context": context()}).json()["session_id"]
    chat = http.post(f"/api/v1/ai/sessions/{session_id}/messages", json={
        "message_id": "first", "revision": 1, "attachments": [attachment()],
    }).json()
    finish_chat(application, session_id, chat["chat_id"], http.headers["X-Project-Id"], 1)
    with pytest.raises(ApplicationConflict):
        application.add_message(session_id, MessageRequest(
            message_id="second", revision=2,
            attachments=[{**attachment(), "read_url": "https://storage.test/different.png"}],
        ), http.headers["X-Project-Id"])
    with pytest.raises(ApplicationInvalid):
        application.add_message(session_id, MessageRequest(
            message_id="second", revision=2, text="계속",
            context={**context(), "source_images": []},
        ), http.headers["X-Project-Id"])
