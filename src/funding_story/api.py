import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from .application import ApplicationConflict, ApplicationInvalid
from .bootstrap import application, close_pools, open_pools
from .content_insights.api import router as content_insights_router
from .http_contract import API_PREFIX
from .models import (
    ChatAcceptedResponse,
    ChatDoneEvent,
    ConfirmRequest,
    ConfirmResponse,
    ErrorResponse,
    LatestSessionResponse,
    MessageRequest,
    RunAcceptedResponse,
    RunRequest,
    SessionCreateRequest,
    SessionResponse,
)
from .observability import emit
from .security import Project


@asynccontextmanager
async def lifespan(app):
    open_pools()
    try:
        yield
    finally:
        close_pools()


app = FastAPI(title="Fundit Funding Story AI", version="1.0.0", lifespan=lifespan)
app.include_router(content_insights_router)


def error(status_code: int, code: str, message: str, detail=None) -> JSONResponse:
    body = ErrorResponse(code=code, message=message, detail=detail)
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    details = [
        {key: value for key, value in item.items() if key in ("type", "loc", "msg")}
        for item in exc.errors()
    ]
    return error(400, "INVALID_INPUT", "요청 형식이 올바르지 않습니다.", details)


@app.exception_handler(LookupError)
async def not_found(request: Request, exc: LookupError):
    return error(404, "NOT_FOUND", "대상을 찾을 수 없습니다.")


@app.exception_handler(ApplicationConflict)
async def conflict(request: Request, exc: ApplicationConflict):
    return error(409, "CONFLICT", str(exc))


@app.exception_handler(ApplicationInvalid)
async def invalid(request: Request, exc: ApplicationInvalid):
    return error(422, "NOT_READY_TO_GENERATE", str(exc))


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    codes = {
        400: "INVALID_INPUT",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        422: "INVALID_PROJECT_DATA",
        429: "TOO_MANY_REQUESTS",
        503: "DEPENDENCY_FAILURE",
    }
    message = exc.detail if isinstance(exc.detail, str) else "요청을 처리할 수 없습니다."
    return error(exc.status_code, codes.get(exc.status_code, "INTERNAL_ERROR"), message)


def kick(record_id: str) -> None:
    emit("job_queued", run_id=record_id)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/ready")
def ready():
    ok, reason = application.readiness()
    if not ok:
        raise HTTPException(503, reason)
    return {"status": "ready"}


@app.post(
    API_PREFIX + "/sessions",
    status_code=201,
    response_model=SessionResponse,
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
def create_session(body: SessionCreateRequest, project: Project):
    return application.create_session(project, body)


@app.get(API_PREFIX + "/sessions/latest", response_model=LatestSessionResponse)
def latest_session(project: Project):
    return {"session": application.latest_session(project)}


@app.get(API_PREFIX + "/sessions/{session_id}", response_model=SessionResponse)
def get_session(session_id: str, project: Project):
    return application.get_session(session_id, project)


@app.post(
    API_PREFIX + "/sessions/{session_id}/start",
    status_code=202,
    response_model=ChatAcceptedResponse,
)
def start_session(session_id: str, project: Project):
    result, should_dispatch = application.start_session(session_id, project)
    if should_dispatch:
        kick(result["chat_id"])
    return result


@app.post(
    API_PREFIX + "/sessions/{session_id}/messages",
    status_code=202,
    response_model=ChatAcceptedResponse,
)
def add_message(session_id: str, body: MessageRequest, project: Project):
    result, should_dispatch = application.add_message(session_id, body, project)
    if should_dispatch:
        kick(result["chat_id"])
    return result


@app.get(API_PREFIX + "/chats/{chat_id}/events")
def chat_events(chat_id: str, project: Project):
    application.get_chat(chat_id, project)

    async def stream():
        previous = ""
        for _ in range(600):
            result = await asyncio.to_thread(application.get_chat, chat_id, project)
            reply = result.get("reply", "")
            if reply != previous:
                yield (
                    "event: message\ndata: "
                    + json.dumps({"chat_id": chat_id, "text": reply}, ensure_ascii=False)
                    + "\n\n"
                )
                previous = reply
            if result["status"] not in ("queued", "running"):
                done = ChatDoneEvent.model_validate(
                    {
                        "chat_id": chat_id,
                        "status": result["status"],
                        "session_id": result["session_id"],
                        "revision": result["revision"],
                        "error": result["error"],
                    }
                )
                yield "event: done\ndata: " + done.model_dump_json() + "\n\n"
                return
            yield ": heartbeat\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post(
    API_PREFIX + "/sessions/{session_id}/confirm",
    response_model=ConfirmResponse,
)
def confirm_session(session_id: str, body: ConfirmRequest, project: Project):
    return application.confirm_session(session_id, body, project)


@app.post(
    API_PREFIX + "/runs",
    status_code=202,
    response_model=RunAcceptedResponse,
)
def create_run(body: RunRequest, project: Project):
    result, should_dispatch = application.create_run(body, project)
    if should_dispatch:
        kick(result["run_id"])
    return result
