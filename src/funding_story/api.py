import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse

from . import assets
from .application import (
    ApplicationConflict,
    ApplicationGone,
    ApplicationInvalid,
    ExportRenderingFailed,
)
from .bootstrap import application, close_pools, open_pools
from .config import settings
from .models import (
    ConfirmRequest,
    ExportCommitRequest,
    ExportRequest,
    ExportResult,
    MessageRequest,
    ProjectInput,
    RunRequest,
)
from .observability import emit
from .renderer import render_scene
from .tasks import execute


@asynccontextmanager
async def lifespan(app):
    open_pools()
    try:
        yield
    finally:
        close_pools()


app = FastAPI(title="Fundit Funding Story AI", version="0.2.0", lifespan=lifespan)


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


@app.exception_handler(ApplicationConflict)
async def conflict(request, exc):
    return JSONResponse({"detail": str(exc)}, 409)


@app.exception_handler(ApplicationInvalid)
async def invalid(request, exc):
    return JSONResponse({"detail": str(exc)}, 422)


@app.exception_handler(ApplicationGone)
async def gone(request, exc):
    return JSONResponse({"detail": str(exc)}, 410)


@app.exception_handler(ExportRenderingFailed)
async def export_failed(request, exc):
    return JSONResponse({"detail": str(exc)}, 422)


def kick(rid):
    try:
        execute.delay(rid)
    except Exception:  # noqa: BLE001 - persist broker delivery for outbox recovery
        emit("outbox_waiting", run_id=rid)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/ready")
def ready():
    ok, reason = application.readiness()
    if not ok:
        raise HTTPException(503, reason)
    return {"status": "ready"}


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
    row = application.get(aid, project, kind="asset")
    return {"asset_id": aid, "mime": row["data"]["mime"], "size": row["data"]["size"]}


@app.post("/v1/assets/import", status_code=201)
def import_asset(body: dict[str, str], project: Project):
    try:
        return {"asset_id": assets.import_s3(project, body.get("key", ""))}
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.post("/v1/sessions", status_code=201)
def session(body: ProjectInput, project: Project):
    return application.create_session(project, body, assets.read)


@app.get("/v1/sessions/latest")
def latest_session(project: Project):
    return {"session": application.latest_session(project)}


@app.get("/v1/sessions/{sid}")
def session_get(sid: str, project: Project):
    return application.get_session(sid, project)


@app.post("/v1/sessions/{sid}/start", status_code=202)
def session_start(sid: str, project: Project):
    """Let the assistant read registered project context and lead the first turn."""
    result, should_dispatch = application.start_session(sid, project)
    if should_dispatch:
        kick(result["id"])
    return result


@app.post("/v1/sessions/{sid}/messages", status_code=202)
def message(sid: str, body: MessageRequest, project: Project):
    result, should_dispatch = application.add_message(sid, body, project, assets.read)
    if should_dispatch:
        kick(result["id"])
    return result


@app.get("/v1/chats/{rid}/events")
def events(rid: str, project: Project):
    application.get_chat(rid, project)

    async def stream():
        previous = ""
        for _ in range(600):
            result = await asyncio.to_thread(application.get_chat, rid, project)
            reply = result.get("reply", "")
            if reply != previous:
                yield (
                    "event: message\ndata: "
                    + json.dumps({"id": rid, "text": reply}, ensure_ascii=False)
                    + "\n\n"
                )
                previous = reply
            if result["status"] not in ("queued", "running"):
                yield "event: done\ndata: " + json.dumps(result, ensure_ascii=False) + "\n\n"
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
    return application.confirm_session(sid, body, project)


@app.post("/v1/runs", status_code=202)
def run(body: RunRequest, project: Project):
    result, should_dispatch = application.create_run(body, project)
    if should_dispatch:
        kick(result["id"])
    return result


@app.get("/v1/runs/{rid}")
def get_run(rid: str, project: Project):
    return application.get_run(rid, project)


@app.post("/v1/runs/{rid}/retry", status_code=202)
def retry(rid: str, project: Project):
    result = application.retry_run(rid, project)
    kick(rid)
    return result


@app.post("/v1/runs/{rid}/exports", status_code=201, response_model=ExportResult)
def export_run(rid: str, body: ExportRequest, project: Project):
    return application.export_run(rid, body, project, render_scene, assets.put)


@app.get("/v1/exports/{eid}", response_model=ExportResult)
def get_export(eid: str, project: Project):
    return application.get_export(eid, project)


@app.post("/v1/exports/{eid}/commit", response_model=ExportResult)
def commit_export(eid: str, body: ExportCommitRequest, project: Project):
    result, cleanup_error = application.commit_export(eid, body, project, assets.delete)
    if cleanup_error:
        emit("temporary_cleanup_waiting", export_id=eid, error_type=cleanup_error)
    return result
