import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from funding_story.api import app
from funding_story.config import settings
from funding_story.infrastructure.persistence import connection, repository

records = repository()


@pytest.fixture
def client(monkeypatch, postgres_container):
    monkeypatch.setattr("funding_story.api.kick", lambda _: None)
    with connection() as conn:
        conn.execute("SELECT 1")
    test_client = TestClient(
        app, headers={"Authorization": "Bearer " + settings().ai_service_token, "X-Project-Id": str(uuid4())}
    )
    yield test_client
    with connection() as conn:
        conn.execute(
            "UPDATE ai_records SET data=jsonb_set(data, '{status}', '\"test_complete\"') WHERE project_id=%s AND kind IN ('run','chat')",
            (test_client.headers["X-Project-Id"],),
        )


def test_auth_and_project_isolation(client):
    x = json.loads(Path("tests/fixtures/appliance.json").read_text())
    sid = client.post("/v1/sessions", json=x).json()["id"]
    assert client.get("/v1/sessions/" + sid).status_code == 200
    assert client.get("/v1/sessions/" + sid, headers={"X-Project-Id": str(uuid4())}).status_code == 404
    assert client.get("/v1/sessions/" + sid, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post("/v1/sessions/" + sid + "/confirm", json={"revision": 1}).status_code == 422


def test_duplicate_chat_and_version(client):
    x = json.loads(Path("tests/fixtures/appliance.json").read_text())
    sid = client.post("/v1/sessions", json=x).json()["id"]
    body = {"message_id": "same", "revision": 1, "text": "강점 정리"}
    first = client.post(f"/v1/sessions/{sid}/messages", json=body)
    second = client.post(f"/v1/sessions/{sid}/messages", json=body)
    assert first.status_code == 202 and first.json()["id"] == second.json()["id"]
    assert client.post(f"/v1/sessions/{sid}/messages", json={**body, "text": "변경"}).status_code == 409


def test_run_confirmation_and_duplicate(client):
    from test_contracts import review

    x = json.loads(Path("tests/fixtures/appliance.json").read_text())
    sid = client.post("/v1/sessions", json=x).json()["id"]
    body = {"session_id": sid, "revision": 1, "idempotency_key": "same"}
    assert client.post("/v1/runs", json=body).status_code == 409
    row = records.get(sid)
    row["data"]["review"] = review().model_dump()
    records.save(sid, row["data"])
    assert client.post(f"/v1/sessions/{sid}/confirm", json={"revision": 1}).status_code == 200
    first = client.post("/v1/runs", json=body)
    assert first.status_code == 202
    assert client.post("/v1/runs", json=body).json()["id"] == first.json()["id"]
    assert client.post("/v1/runs", json={**body, "revision": 2}).status_code == 409
    row = records.get(first.json()["id"])
    row["data"]["status"] = "failed"
    records.save(row["id"], row["data"])


def test_asset_isolation(client):
    aid = client.post(
        "/v1/assets",
        files={"file": ("original.png", Path("tests/fixtures/original.png").read_bytes(), "image/png")},
    ).json()["asset_id"]
    assert client.get("/v1/assets/" + aid).status_code == 200
    assert client.get("/v1/assets/" + aid, headers={"X-Project-Id": str(uuid4())}).status_code == 404


def test_intake_can_begin_without_description_or_images_and_resume(client):
    response = client.post("/v1/sessions", json={"title": "LUMI S1"})
    assert response.status_code == 201
    sid = response.json()["id"]
    latest = client.get("/v1/sessions/latest").json()["session"]
    assert latest["id"] == sid and latest["input"]["product_description"] == ""
    assert client.get("/v1/sessions/latest", headers={"X-Project-Id": str(uuid4())}).json()["session"] is None
    assert client.post(f"/v1/sessions/{sid}/confirm", json={"revision": 1}).status_code == 422
    assert (
        client.post(
            f"/v1/sessions/{sid}/messages",
            json={"message_id": "first", "revision": 1, "text": "무선 청소기예요."},
        ).status_code
        == 202
    )


def test_session_start_queues_assistant_led_first_turn_once(client):
    x = json.loads(Path("tests/fixtures/appliance.json").read_text())
    created = client.post("/v1/sessions", json=x).json()

    first = client.post(f"/v1/sessions/{created['id']}/start")
    second = client.post(f"/v1/sessions/{created['id']}/start")

    assert first.status_code == 202
    assert first.json()["mode"] == "initial"
    assert second.status_code == 202 and second.json()["id"] == first.json()["id"]
    session = client.get(f"/v1/sessions/{created['id']}").json()
    assert session["revision"] == 2
    assert session["messages"] == []
    assert session["active_chat"] == first.json()["id"]


def test_chat_rejects_foreign_asset_without_changing_session(client):
    sid = client.post("/v1/sessions", json={"title": "LUMI S1"}).json()["id"]
    foreign = records.create(str(uuid4()), "asset", {"mime": "image/png"})
    response = client.post(
        f"/v1/sessions/{sid}/messages",
        json={"message_id": "image", "revision": 1, "text": "사진입니다.", "asset_ids": [foreign]},
    )
    assert response.status_code == 404
    session = client.get(f"/v1/sessions/{sid}").json()
    assert session["revision"] == 1 and session["input"]["asset_ids"] == []


def test_export_returns_png_text_manifest_and_commit_clears_temporary_design(client):
    project = client.headers["X-Project-Id"]
    source = Path("tests/fixtures/original.png").read_bytes()
    source_asset = client.post("/v1/assets", files={"file": ("original.png", source, "image/png")}).json()[
        "asset_id"
    ]
    scene = {
        "version": 1,
        "templateId": "appliance-reference-konva",
        "revision": 24,
        "brand": "#647895",
        "blocks": [
            {
                "id": "hero",
                "label": "프로젝트 소개",
                "width": 860,
                "height": 320,
                "nodes": [
                    {
                        "id": "hero.image",
                        "kind": "image",
                        "x": 0,
                        "y": 0,
                        "width": 860,
                        "height": 320,
                        "assetId": source_asset,
                        "fit": "cover",
                        "focalX": 0.5,
                        "focalY": 0.5,
                        "desc": "제품",
                    },
                    {
                        "id": "hero.pending-image",
                        "kind": "image",
                        "x": 20,
                        "y": 20,
                        "width": 80,
                        "height": 80,
                        "assetId": "",
                        "pending": True,
                        "fit": "cover",
                        "focalX": 0.5,
                        "focalY": 0.5,
                        "desc": "사용자가 편집할 빈 이미지 슬롯",
                    },
                    {
                        "id": "hero.title",
                        "kind": "text",
                        "x": 80,
                        "y": 90,
                        "width": 700,
                        "height": 100,
                        "text": "원래 문구",
                        "fontFamily": "Pretendard",
                        "fontSize": 52,
                        "fontWeight": "700",
                        "lineHeight": 1.2,
                        "letterSpacing": -1,
                        "align": "center",
                        "fill": "#FFFFFF",
                    },
                ],
            }
        ],
    }
    rid = records.create(
        project,
        "run",
        {
            "status": "succeeded",
            "source_revision": 4,
            "snapshot": {"private": "temporary"},
            "document": {
                "schema_version": 1,
                "scene": scene,
                "information": {"budget": "부품 확보와 검수에 사용합니다."},
                "summary": "요약",
                "storyline": "스토리라인",
            },
        },
    )
    body = {
        "source_input_revision": 4,
        "idempotency_key": "export-once",
        "text_overrides": {"hero.title": "수정 문구"},
    }
    response = client.post(f"/v1/runs/{rid}/exports", json=body)
    assert response.status_code == 201
    result = response.json()
    assert result["status"] == "succeeded" and result["information"]["budget"].startswith("부품")
    assert result["project_summary"] == {"summary": "요약", "storyline": "스토리라인"}
    assert "text_overrides" not in result and len(result["images"]) == 1
    image = client.get("/v1/assets/" + result["images"][0]["asset_id"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert image.content.startswith(b"\x89PNG")
    assert client.post(f"/v1/runs/{rid}/exports", json=body).json()["id"] == result["id"]

    committed = client.post(f"/v1/exports/{result['id']}/commit", json={"document_revision": 7})
    assert committed.status_code == 200 and committed.json()["committed"] is True
    assert committed.json()["cleanup_pending"] is False
    assert client.post(f"/v1/exports/{result['id']}/commit", json={"document_revision": 7}).status_code == 200
    assert client.post(f"/v1/exports/{result['id']}/commit", json={"document_revision": 8}).status_code == 409
    run = records.get(rid)["data"]
    assert "document" not in run and "snapshot" not in run and run["temporary_design_cleared"]
    assert client.get(f"/v1/exports/{result['id']}").json()["images"] == result["images"]


def test_export_rejects_partial_run_unknown_slot_and_stale_revision(client):
    project = client.headers["X-Project-Id"]
    rid = records.create(
        project,
        "run",
        {
            "status": "partially_succeeded",
            "source_revision": 2,
            "document": {"scene": {"blocks": []}},
        },
    )
    body = {"source_input_revision": 2, "idempotency_key": "partial", "text_overrides": {}}
    assert client.post(f"/v1/runs/{rid}/exports", json=body).status_code == 409
    row = records.get(rid)
    row["data"]["status"] = "succeeded"
    row["data"]["document"] = {"scene": {"blocks": []}}
    records.save(rid, row["data"])
    assert (
        client.post(
            f"/v1/runs/{rid}/exports",
            json={**body, "idempotency_key": "unknown", "text_overrides": {"unknown": "x"}},
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"/v1/runs/{rid}/exports",
            json={**body, "idempotency_key": "stale", "source_input_revision": 1},
        ).status_code
        == 409
    )
