from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from funding_story.api import app
from funding_story.bootstrap import application
from funding_story.config import settings

PREFIX = "/api/v1/ai"


def context(title="작은 공간을 위한 공기청정기"):
    return {
        "project": {
            "business_type": "SOLE",
            "category": {"major": "테크·가전", "minor": "생활가전"},
            "title": title,
            "goal_amount": 3_000_000,
        },
        "rewards": [
            {
                "reward_id": 101,
                "name": "얼리버드 1대",
                "description": "공기청정기 본품 1대",
                "price": 129_000,
                "is_limited": True,
                "quantity": 100,
                "is_early_bird": True,
                "options": [{"group_name": "색상", "values": ["화이트", "그레이"]}],
            }
        ],
        "source_images": [],
    }


def review():
    return {
        "reply": "제품과 이야기, 강점을 확인해 주세요.",
        "product": "작은 공간을 위한 저소음 공기청정기",
        "story": "생활 공간의 소음 부담을 줄이기 위해 만든 이야기",
        "strengths": [
            {"id": str(index), "title": f"강점 {index}", "description": f"설명 {index}"}
            for index in range(1, 4)
        ],
        "problems": [{"heading": f"문제 {index}", "body": f"상황 {index}"} for index in range(1, 5)],
        "missing": [],
        "tone": None,
        "brand_color": None,
    }


@pytest.fixture
def client(monkeypatch):
    project = str(uuid4())
    monkeypatch.setattr("funding_story.api.open_pools", lambda: None)
    monkeypatch.setattr("funding_story.api.close_pools", lambda: None)
    monkeypatch.setattr("funding_story.api.kick", lambda _: None)
    with TestClient(
        app,
        headers={
            "Authorization": "Bearer " + settings().ai_service_token,
            "X-Project-Id": project,
        },
    ) as test_client:
        yield test_client


def test_session_chat_confirm_and_run_follow_v1_contract(client):
    created = client.post(PREFIX + "/sessions", json={"context": context()})
    assert created.status_code == 201
    session = created.json()
    assert set(session) == {
        "session_id",
        "revision",
        "confirmed_revision",
        "messages",
        "missing",
        "summary",
        "active_chat_id",
    }

    latest = client.get(PREFIX + "/sessions/latest").json()["session"]
    assert latest["session_id"] == session["session_id"]

    first = client.post(PREFIX + f"/sessions/{session['session_id']}/start")
    duplicate = client.post(PREFIX + f"/sessions/{session['session_id']}/start")
    assert first.status_code == 202
    assert duplicate.json() == first.json()

    application.complete_chat(
        session_id=session["session_id"],
        chat_id=first.json()["chat_id"],
        project=client.headers["X-Project-Id"],
        source_revision=1,
        review=review(),
        reply=review()["reply"],
    )
    events = client.get(PREFIX + f"/chats/{first.json()['chat_id']}/events")
    assert '"chat_id"' in events.text and "event: done" in events.text

    current = client.get(PREFIX + f"/sessions/{session['session_id']}").json()
    assert current["revision"] == 2
    assert current["missing"] == []
    assert current["summary"]["product"].startswith("작은 공간")

    confirmed = client.post(
        PREFIX + f"/sessions/{session['session_id']}/confirm",
        json={"revision": 2},
    )
    assert confirmed.json() == {"session_id": session["session_id"], "confirmed_revision": 2}

    request = {
        "session_id": session["session_id"],
        "confirmed_revision": 2,
        "idempotency_key": "full-generation-1",
        "context": context(),
    }
    accepted = client.post(PREFIX + "/runs", json=request)
    duplicate_run = client.post(PREFIX + "/runs", json=request)
    assert accepted.status_code == 202
    assert duplicate_run.json() == accepted.json()
    assert set(accepted.json()) == {"run_id", "status"}


def test_message_idempotency_project_scope_and_error_shape(client):
    session_id = client.post(PREFIX + "/sessions", json={"context": context()}).json()["session_id"]
    body = {"message_id": "message-1", "revision": 1, "text": "사용 장면을 알려드릴게요."}
    first = client.post(PREFIX + f"/sessions/{session_id}/messages", json=body)
    duplicate = client.post(PREFIX + f"/sessions/{session_id}/messages", json=body)
    assert duplicate.json() == first.json()

    conflict = client.post(
        PREFIX + f"/sessions/{session_id}/messages",
        json={**body, "text": "다른 내용"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "CONFLICT"

    foreign = client.get(
        PREFIX + f"/sessions/{session_id}",
        headers={"X-Project-Id": str(uuid4())},
    )
    assert foreign.status_code == 404
    assert foreign.json() == {
        "code": "NOT_FOUND",
        "message": "대상을 찾을 수 없습니다.",
        "detail": None,
    }

    unauthorized = client.get(
        PREFIX + f"/sessions/{session_id}",
        headers={"Authorization": "Bearer wrong"},
    )
    assert unauthorized.status_code == 401
    assert unauthorized.json()["code"] == "UNAUTHORIZED"


def test_project_scope_accepts_be_uuid_v7_and_rejects_non_uuid(client):
    valid = client.get(
        PREFIX + "/sessions/latest",
        headers={"X-Project-Id": "018f2c1a-3b4e-7a12-9c9d-0a1b2c3d4e5f"},
    )
    assert valid.status_code == 200

    invalid = client.get(
        PREFIX + "/sessions/latest",
        headers={"X-Project-Id": "project-1"},
    )
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "INVALID_INPUT"


def test_legacy_fields_and_routes_are_not_accepted(client):
    invalid = client.post(
        PREFIX + "/sessions",
        json={"context": context(), "asset_ids": ["legacy"]},
    )
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "INVALID_INPUT"

    assert client.post("/v1/sessions", json={"context": context()}).status_code == 404
    assert client.post(PREFIX + "/assets").status_code == 404
    assert client.get(PREFIX + "/runs/not-an-ai-query").status_code == 404
    assert client.post(PREFIX + "/runs/not-an-ai-query/retry").status_code == 404
