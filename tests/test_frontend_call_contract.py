import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from funding_story import tasks
from funding_story.api import app
from funding_story.config import settings
from funding_story.infrastructure.persistence import connection, repository
from funding_story.models import CopyResult, ProjectInput, Review
from funding_story.planner import plan

records = repository()


@pytest.fixture
def frontend_client(monkeypatch, postgres_container):
    monkeypatch.setattr("funding_story.api.kick", lambda _: None)
    project = str(uuid4())
    client = TestClient(
        app,
        headers={"Authorization": "Bearer " + settings().ai_service_token, "X-Project-Id": project},
    )
    yield client
    with connection() as conn:
        conn.execute(
            "UPDATE ai_records SET data=jsonb_set(data, '{status}', '\"test_complete\"') "
            "WHERE project_id=%s AND kind IN ('run','chat')",
            (project,),
        )


def confirmed_review():
    return Review(
        reply="입력 내용을 정리했습니다.",
        strengths=[
            {"id": "weight", "title": "가벼운 본체", "description": "약 1.3 kg"},
            {"id": "handy", "title": "핸디 전환", "description": "연장관 분리"},
            {"id": "filter", "title": "필터 관리", "description": "세척 가능한 1차 필터"},
        ],
        problems=[
            {"heading": "무거운 본체", "body": "꺼내 들기 부담스러워요."},
            {"heading": "좁은 틈", "body": "가구 사이를 청소하기 어려워요."},
            {"heading": "먼지 확인", "body": "비울 시점을 알기 어려워요."},
            {"heading": "도구 교체", "body": "청소 위치마다 준비가 번거로워요."},
        ],
    )


def test_frontend_request_sequence_reaches_png_and_text_result(frontend_client, monkeypatch):
    source = Path("tests/fixtures/original.png").read_bytes()
    uploaded = frontend_client.post("/v1/assets", files={"file": ("product.png", source, "image/png")})
    assert uploaded.status_code == 201
    fixture = json.loads(Path("tests/fixtures/appliance.json").read_text())
    fixture["asset_ids"] = [uploaded.json()["asset_id"]]
    created = frontend_client.post("/v1/sessions", json=fixture)
    assert created.status_code == 201
    sid = created.json()["id"]

    accepted = frontend_client.post(
        f"/v1/sessions/{sid}/messages",
        json={"message_id": "ui-message-1", "revision": 1, "text": "이 내용으로 정리해 주세요."},
    )
    assert accepted.status_code == 202
    chat_id = accepted.json()["id"]
    review = confirmed_review()
    monkeypatch.setattr(tasks, "generate_checked", lambda *args, **kwargs: review)
    monkeypatch.setattr(tasks.provider, "chat_stream", lambda prompt: iter([review.reply]))
    tasks.chat(records.get(chat_id))
    with frontend_client.stream("GET", f"/v1/chats/{chat_id}/events") as stream:
        events = "\n".join(stream.iter_lines())
    assert "event: done" in events and "입력 내용을 정리했습니다." in events

    session = frontend_client.get(f"/v1/sessions/{sid}").json()
    revision = session["revision"]
    assert len(session["review"]["strengths"]) == 3 and len(session["review"]["problems"]) == 4
    assert frontend_client.post(f"/v1/sessions/{sid}/confirm", json={"revision": revision}).status_code == 200
    run_response = frontend_client.post(
        "/v1/runs",
        json={"session_id": sid, "revision": revision, "idempotency_key": "ui-generate-1"},
    )
    assert run_response.status_code == 202
    rid = run_response.json()["id"]

    info = ProjectInput.model_validate(session["input"])
    scene, fixed = plan(info, review)
    draft = CopyResult(
        texts={
            node["id"]: node["text"]
            for block in scene["blocks"]
            for node in block["nodes"]
            if node["kind"] == "text" and node["id"] not in fixed
        },
        image_prompts={
            node["id"]: "입력 제품과 같은 외형의 제품 사진"
            for block in scene["blocks"]
            for node in block["nodes"]
            if node["kind"] == "image"
        },
        summary="가벼운 무선 청소기",
        storyline="좁은 공간을 자주 청소하는 사용자를 위한 가상 제품입니다.",
    )
    monkeypatch.setattr(tasks, "generate_checked", lambda *args, **kwargs: draft)
    monkeypatch.setattr(tasks.provider, "image", lambda prompt, references: (source, "image/png"))
    tasks.generate(records.get(rid))
    generated = frontend_client.get(f"/v1/runs/{rid}")
    assert generated.status_code == 200 and generated.json()["status"] == "succeeded"
    temporary_asset = next(
        job["asset_id"]
        for job in records.get(rid)["data"]["image_jobs"].values()
        if job["status"] == "succeeded"
    )

    exported = frontend_client.post(
        f"/v1/runs/{rid}/exports",
        json={
            "source_input_revision": revision,
            "idempotency_key": "ui-export-1",
            "text_overrides": {"hero.detail-0": "제약 없는\n무선 청소"},
        },
    )
    assert exported.status_code == 201
    result = exported.json()
    assert result["status"] == "succeeded" and len(result["images"]) == len(scene["blocks"])
    assert result["information"] == {k: v for k, v in fixture["information"].items() if k != "gift_details"}
    assert "gift_details" not in result["information"]
    assert result["fixed_content"]["crowdfunding_notice_key"].startswith("fundit.")
    committed = frontend_client.post(f"/v1/exports/{result['id']}/commit", json={"document_revision": 1})
    assert committed.status_code == 200 and committed.json()["cleanup_pending"] is False
    assert frontend_client.get("/v1/assets/" + temporary_asset).status_code == 404
    assert frontend_client.get("/v1/assets/" + result["images"][0]["asset_id"]).status_code == 200


def test_first_turn_uses_registered_context_before_user_message(frontend_client, monkeypatch):
    fixture = json.loads(Path("tests/fixtures/appliance.json").read_text())
    created = frontend_client.post("/v1/sessions", json=fixture).json()
    accepted = frontend_client.post(f"/v1/sessions/{created['id']}/start")
    assert accepted.status_code == 202

    review = Review(
        reply=(
            f"등록하신 ‘{fixture['title']}’ 정보와 선물 구성을 확인했어요. "
            "이 제품을 만들게 된 계기와 가장 먼저 보여주고 싶은 사용 장면을 알려주세요."
        ),
        missing=["제작 계기", "강조할 사용 장면"],
    )
    monkeypatch.setattr(tasks, "generate_checked", lambda *args, **kwargs: review)
    tasks.chat(records.get(accepted.json()["id"]))

    session = frontend_client.get(f"/v1/sessions/{created['id']}").json()
    assert session["input"] == created["input"]
    assert session["messages"] == [{"role": "assistant", "text": review.reply}]
    assert session["review"]["missing"] == ["제작 계기", "강조할 사용 장면"]
    assert session["active_chat"] is None
