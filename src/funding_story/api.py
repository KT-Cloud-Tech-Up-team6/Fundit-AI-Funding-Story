import asyncio
import copy
import hashlib
import json
import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse

from . import assets, store
from .config import settings
from .models import (
    ConfirmRequest,
    ExportCommitRequest,
    ExportRequest,
    ExportResult,
    MessageRequest,
    ProjectInput,
    Review,
    RunRequest,
)
from .renderer import render_scene
from .tasks import execute

app = FastAPI(title="Fundit Funding Story AI", version="0.2.0")


def authorize(
    authorization: Annotated[str | None, Header()] = None,
    x_project_id: Annotated[str | None, Header()] = None,
):
    if not authorization or not secrets.compare_digest(
        authorization, "Bearer " + settings().ai_service_token
    ):
        raise HTTPException(401, "내부 서비스 인증이 필요합니다.")
    if not x_project_id:
        raise HTTPException(400, "프로젝트 범위가 필요합니다.")
    return x_project_id


Project = Annotated[str, Depends(authorize)]


@app.exception_handler(LookupError)
async def not_found(request, exc):
    return Response('{"detail":"대상을 찾을 수 없습니다."}', 404, media_type="application/json")


def kick(rid):
    try:
        execute.delay(rid)
    except Exception:  # noqa: BLE001 - persist broker delivery for outbox recovery
        store.emit("outbox_waiting", run_id=rid)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/v1/assets", status_code=201)
def upload(project: Project, file: UploadFile):
    try:
        return {"asset_id": assets.put(project, file.file.read(20 * 1024 * 1024 + 1))}
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.get("/v1/assets/{aid}")
def download(aid: str, project: Project):
    data, mime = assets.read(aid, project)
    return Response(data, media_type=mime, headers={"Cache-Control": "private, max-age=300"})


@app.get("/v1/assets/{aid}/metadata")
def asset_metadata(aid: str, project: Project):
    row = store.get(aid, project, kind="asset")
    return {"asset_id": aid, "mime": row["data"]["mime"], "size": row["data"]["size"]}


@app.post("/v1/assets/import", status_code=201)
def import_asset(body: dict[str, str], project: Project):
    try:
        return {"asset_id": assets.import_s3(project, body.get("key", ""))}
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.post("/v1/sessions", status_code=201)
def session(body: ProjectInput, project: Project):
    for aid in body.asset_ids:
        assets.read(aid, project)
    sid = store.create(
        project,
        "session",
        {"input": body.model_dump(), "messages": [], "review": None, "confirmed_revision": None},
    )
    return store.public(store.get(sid, project, kind="session"))


@app.get("/v1/sessions/latest")
def latest_session(project: Project):
    with store.connection() as conn:
        row = conn.execute(
            "SELECT * FROM ai_records WHERE project_id=%s AND kind='session' ORDER BY updated_at DESC, id DESC LIMIT 1",
            (project,),
        ).fetchone()
    return {"session": store.public(row) if row else None}


@app.get("/v1/sessions/{sid}")
def session_get(sid: str, project: Project):
    return store.public(store.get(sid, project, kind="session"))


@app.post("/v1/sessions/{sid}/messages", status_code=202)
def message(sid: str, body: MessageRequest, project: Project):
    with store.connection() as conn:
        row = store.get(sid, project, conn, True, kind="session")
        data = row["data"]
        for message in data["messages"]:
            if message.get("message_id") == body.message_id:
                if message["text"] != body.text or message.get("asset_ids", []) != body.asset_ids:
                    raise HTTPException(409, "동일 요청 ID의 내용이 다릅니다.")
                return store.public(store.get(message["job_id"], project, conn))
        if row["revision"] != body.revision:
            raise HTTPException(409, "입력 버전이 변경되었습니다.")
        if data.get("active_chat") and store.get(data["active_chat"], project, conn)["data"]["status"] in (
            "queued",
            "running",
        ):
            raise HTTPException(409, "이전 답변을 처리 중입니다.")
        asset_ids = list(dict.fromkeys(data["input"]["asset_ids"] + body.asset_ids))
        if len(asset_ids) > 30:
            raise HTTPException(422, "이미지는 최대 30개입니다.")
        for aid in body.asset_ids:
            assets.read(aid, project)
        data["input"]["asset_ids"] = asset_ids
        rid = store.create(
            project,
            "chat",
            {"status": "queued", "session_id": sid, "source_revision": row["revision"] + 1, "reply": ""},
            conn,
        )
        data["messages"].append(
            {
                "role": "user",
                "text": body.text,
                "message_id": body.message_id,
                "job_id": rid,
                "asset_ids": body.asset_ids,
            }
        )
        data.update(active_chat=rid, confirmed_revision=None)
        store.save(sid, data, conn, row["revision"] + 1)
    kick(rid)
    return store.public(store.get(rid, project))


@app.get("/v1/chats/{rid}/events")
def events(rid: str, project: Project):
    store.get(rid, project, kind="chat")

    async def stream():
        previous = ""
        for _ in range(600):
            row = await asyncio.to_thread(store.get, rid, project)
            data = row["data"]
            reply = data.get("reply", "")
            if reply != previous:
                yield (
                    "event: message\ndata: "
                    + json.dumps({"id": rid, "text": reply}, ensure_ascii=False)
                    + "\n\n"
                )
                previous = reply
            if data["status"] not in ("queued", "running"):
                yield "event: done\ndata: " + json.dumps(store.public(row), ensure_ascii=False) + "\n\n"
                return
            yield ": heartbeat\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/v1/sessions/{sid}/confirm")
def confirm(sid: str, body: ConfirmRequest, project: Project):
    with store.connection() as conn:
        row = store.get(sid, project, conn, True, kind="session")
        if row["revision"] != body.revision or row["data"].get("active_chat"):
            raise HTTPException(409, "최신 답변을 확인해 주세요.")
        if not row["data"].get("review"):
            raise HTTPException(422, "핵심 강점 정리가 필요합니다.")
        review = Review.model_validate(row["data"]["review"])
        if review.missing or len(review.problems) != 4 or len(review.strengths) < 3:
            raise HTTPException(422, "부족한 정보를 먼저 보완해 주세요.")
        row["data"]["confirmed_revision"] = body.revision
        store.save(sid, row["data"], conn)
    return {"confirmed_revision": body.revision}


@app.post("/v1/runs", status_code=202)
def run(body: RunRequest, project: Project):
    fingerprint = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
    with store.connection() as conn:
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (project + body.idempotency_key,)
        )
        session = store.get(body.session_id, project, conn, True, kind="session")
        existing = conn.execute(
            "SELECT * FROM ai_requests WHERE project_id=%s AND request_key=%s",
            (project, body.idempotency_key),
        ).fetchone()
        if existing:
            if existing["fingerprint"] != fingerprint:
                raise HTTPException(409, "중복 키의 입력이 다릅니다.")
            return store.public(store.get(existing["record_id"], project, conn))
        if session["revision"] != body.revision or session["data"].get("confirmed_revision") != body.revision:
            raise HTTPException(409, "최신 입력을 확인한 뒤 생성해 주세요.")
        rid = store.create(
            project,
            "run",
            {
                "status": "queued",
                "stage": "접수",
                "snapshot": session["data"],
                "source_revision": body.revision,
                "session_id": body.session_id,
                "image_jobs": {},
            },
            conn,
        )
        conn.execute(
            "INSERT INTO ai_requests VALUES(%s,%s,%s,%s)", (project, body.idempotency_key, fingerprint, rid)
        )
    kick(rid)
    return store.public(store.get(rid, project))


@app.get("/v1/runs/{rid}")
def get_run(rid: str, project: Project):
    row = store.get(rid, project, kind="run")
    result = store.public(row)
    result.pop("snapshot", None)
    return result


@app.post("/v1/runs/{rid}/retry", status_code=202)
def retry(rid: str, project: Project):
    with store.connection() as conn:
        row = store.get(rid, project, conn, True, kind="run")
        if row["data"]["status"] not in ("failed", "partially_succeeded"):
            raise HTTPException(409, "재시도 대상이 아닙니다.")
        row["data"].update(status="queued", stage="재시도 대기", error=None)
        if row["data"].get("document") is None:
            row["data"]["retry_cycle"] = row["data"].get("retry_cycle", 0) + 1
        store.save(rid, row["data"], conn)
    kick(rid)
    return {"id": rid, "status": "queued"}


def _export_public(row):
    result = store.public(row)
    result.pop("text_overrides", None)
    result.pop("cleanup_asset_ids", None)
    return result


@app.post("/v1/runs/{rid}/exports", status_code=201, response_model=ExportResult)
def export_run(rid: str, body: ExportRequest, project: Project):
    fingerprint = hashlib.sha256((rid + body.model_dump_json()).encode()).hexdigest()
    request_key = "export:" + rid + ":" + body.idempotency_key
    with store.connection() as conn:
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (project + request_key,)
        )
        run = store.get(rid, project, conn, kind="run")
        existing = conn.execute(
            "SELECT * FROM ai_requests WHERE project_id=%s AND request_key=%s",
            (project, request_key),
        ).fetchone()
        if existing:
            if existing["fingerprint"] != fingerprint:
                raise HTTPException(409, "중복 키의 입력이 다릅니다.")
            return _export_public(store.get(existing["record_id"], project, conn, kind="export"))
        data = run["data"]
        if data.get("status") != "succeeded":
            raise HTTPException(409, "모든 이미지가 완료된 생성 결과만 내보낼 수 있습니다.")
        if data.get("source_revision") != body.source_input_revision:
            raise HTTPException(409, "생성 기준 입력 버전이 다릅니다.")
        document = copy.deepcopy(data.get("document"))
        if not document:
            raise HTTPException(410, "임시 디자인이 이미 정리되었습니다.")
        text_nodes = {
            node["id"]: node
            for block in document["scene"]["blocks"]
            for node in block["nodes"]
            if node["kind"] == "text"
        }
        unknown = sorted(set(body.text_overrides) - set(text_nodes))
        if unknown:
            raise HTTPException(422, "정의되지 않은 문구 슬롯: " + ", ".join(unknown[:5]))
        for node_id, value in body.text_overrides.items():
            text_nodes[node_id]["text"] = value
        eid = store.create(
            project,
            "export",
            {
                "status": "rendering",
                "run_id": rid,
                "source_input_revision": body.source_input_revision,
                "text_overrides": body.text_overrides,
            },
            conn,
        )
        conn.execute(
            "INSERT INTO ai_requests VALUES(%s,%s,%s,%s)",
            (project, request_key, fingerprint, eid),
        )
    try:
        images = render_scene(document["scene"], project)
        output = []
        for index, image in enumerate(images):
            aid = assets.put(project, image["bytes"], "image/png")
            output.append(
                {
                    "block_id": image["block_id"],
                    "order": index,
                    "asset_id": aid,
                    "width": image["width"],
                    "height": image["height"],
                    "alt": image["label"],
                }
            )
        exported = store.get(eid, project)
        exported["data"].update(
            status="succeeded",
            schema_version=1,
            images=output,
            information=document.get("information", {}),
            fixed_content={"crowdfunding_notice_key": "fundit.crowdfunding-notice.pending-v1"},
            project_summary={
                "summary": document.get("summary", ""),
                "storyline": document.get("storyline", ""),
            },
            committed=False,
        )
        store.save(eid, exported["data"])
    except Exception as exc:
        exported = store.get(eid, project)
        exported["data"].update(status="failed", error=type(exc).__name__)
        store.save(eid, exported["data"])
        raise HTTPException(422, "PNG 변환에 실패했습니다.") from exc
    return _export_public(store.get(eid, project, kind="export"))


@app.get("/v1/exports/{eid}", response_model=ExportResult)
def get_export(eid: str, project: Project):
    return _export_public(store.get(eid, project, kind="export"))


@app.post("/v1/exports/{eid}/commit", response_model=ExportResult)
def commit_export(eid: str, body: ExportCommitRequest, project: Project):
    with store.connection() as conn:
        exported = store.get(eid, project, conn, True, kind="export")
        data = exported["data"]
        if data.get("status") != "succeeded":
            raise HTTPException(409, "완료된 PNG 결과만 저장 확인할 수 있습니다.")
        if data.get("committed"):
            if data.get("document_revision") != body.document_revision:
                raise HTTPException(409, "다른 본문 버전으로 이미 저장 확인되었습니다.")
        else:
            run = store.get(data["run_id"], project, conn, True, kind="run")
            data.update(
                committed=True,
                document_revision=body.document_revision,
                committed_at=datetime.now(UTC).isoformat(),
                cleanup_pending=True,
                cleanup_asset_ids=[
                    job["asset_id"]
                    for job in run["data"].get("image_jobs", {}).values()
                    if job.get("status") == "succeeded" and job.get("asset_id")
                ],
            )
            store.save(eid, data, conn)
            run["data"].pop("document", None)
            run["data"].pop("snapshot", None)
            run["data"].pop("image_jobs", None)
            run["data"]["temporary_design_cleared"] = True
            run["data"]["export_id"] = eid
            store.save(run["id"], run["data"], conn)
    try:
        store.delete_run_checkpoints(data["run_id"])
        for asset_id in data.get("cleanup_asset_ids", []):
            try:
                assets.delete(asset_id, project)
            except LookupError:
                pass
        current = store.get(eid, project)
        current["data"]["cleanup_pending"] = False
        store.save(eid, current["data"])
    except Exception as exc:  # noqa: BLE001 - commit remains durable; repeated commit retries cleanup
        store.emit("temporary_cleanup_waiting", export_id=eid, error_type=type(exc).__name__)
    return _export_public(store.get(eid, project, kind="export"))
