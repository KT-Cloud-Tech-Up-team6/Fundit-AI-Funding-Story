import time
from datetime import UTC, datetime
from io import BytesIO
from xml.etree import ElementTree

import httpx
from PIL import Image

from .config import settings
from .models import (
    OutputDescriptor,
    RunCompletionRequest,
    RunCompletionResponse,
    SourceImageRef,
    UploadTargetsRequest,
    UploadTargetsResponse,
)

MAX_IMAGE_BYTES = 10 * 1024 * 1024


class ImageReadUrlExpired(ValueError):
    code = "IMAGE_READ_URL_EXPIRED"

    def __init__(self):
        super().__init__("입력 이미지 읽기 URL이 만료되었습니다.")
        self.slot_id: str | None = None


def _expired_signed_response(response: httpx.Response, expires_at: datetime) -> bool:
    if expires_at <= datetime.now(UTC):
        return True
    # S3 can reject temporary credentials before the declared URL expiry. Only
    # explicit expiry responses qualify; ordinary access-denied errors do not.
    body = bytearray()
    for chunk in response.iter_bytes():
        if len(body) + len(chunk) > 16 * 1024:
            return False
        body.extend(chunk)
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return False
    code = root.findtext("Code")
    message = root.findtext("Message")
    return code in ("ExpiredToken", "RequestExpired") or (
        code == "AccessDenied" and message == "Request has expired"
    )


def _read_image(
    url: str,
    *,
    expected_type: str | None = None,
    expected_size: int | None = None,
    expires_at: datetime | None = None,
) -> tuple[bytes, str]:
    if expires_at is not None and expires_at <= datetime.now(UTC):
        raise ImageReadUrlExpired()
    with httpx.stream(
        "GET",
        url,
        timeout=settings().internal_http_timeout_seconds,
        follow_redirects=False,
    ) as response:
        if (
            expires_at is not None
            and response.status_code in (400, 403)
            and _expired_signed_response(response, expires_at)
        ):
            raise ImageReadUrlExpired()
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type not in ("image/png", "image/jpeg", "image/webp"):
            raise ValueError("지원하지 않는 입력 이미지 MIME입니다.")
        if expected_type is not None and content_type != expected_type:
            raise ValueError("입력 이미지 MIME이 계약과 다릅니다.")
        chunks = []
        size = 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > MAX_IMAGE_BYTES or (expected_size is not None and size > expected_size):
                raise ValueError("입력 이미지 크기가 계약을 초과합니다.")
            chunks.append(chunk)
    content = b"".join(chunks)
    if expected_size is not None and len(content) != expected_size:
        raise ValueError("입력 이미지 크기가 계약과 다릅니다.")
    with Image.open(BytesIO(content)) as image:
        expected_format = {
            "image/png": "PNG",
            "image/jpeg": "JPEG",
            "image/webp": "WEBP",
        }[content_type]
        if image.format != expected_format:
            raise ValueError("지원하지 않는 입력 이미지입니다.")
        image.verify()
    return content, content_type


def read_source_image(reference: SourceImageRef) -> tuple[bytes, str]:
    try:
        return _read_image(
            str(reference.read_url),
            expected_type=reference.content_type,
            expected_size=reference.file_size,
            expires_at=reference.expires_at,
        )
    except ImageReadUrlExpired as exc:
        exc.slot_id = reference.slot_id
        raise


def read_public_image(url: str) -> tuple[bytes, str]:
    return _read_image(url)


class BackendClient:
    def __init__(self, project_id: str):
        cfg = settings()
        if not cfg.internal_api_key:
            raise RuntimeError("INTERNAL_API_KEY 설정이 필요합니다.")
        self._base = cfg.project_service_base_url.rstrip("/")
        self._timeout = cfg.internal_http_timeout_seconds
        self._headers = {
            "X-Internal-Api-Key": cfg.internal_api_key,
            "X-Project-Id": project_id,
        }

    def upload_targets(self, outputs: list[OutputDescriptor]) -> UploadTargetsResponse:
        body = UploadTargetsRequest(outputs=outputs)
        response = httpx.post(
            self._base + "/internal/ai/media/upload-targets",
            headers=self._headers,
            json=body.model_dump(mode="json"),
            timeout=self._timeout,
        )
        response.raise_for_status()
        result = UploadTargetsResponse.model_validate(response.json())
        requested = {output.slot_id for output in outputs}
        returned = {target.slot_id for target in result.targets}
        if requested != returned or len(returned) != len(result.targets):
            raise ValueError("BE 업로드 대상이 요청 슬롯과 일치하지 않습니다.")
        return result

    def upload(self, upload_url: str, content: bytes) -> None:
        response = httpx.put(
            upload_url,
            headers={"Content-Type": "image/png"},
            content=content,
            timeout=self._timeout,
        )
        response.raise_for_status()

    def complete(self, run_id: str, body: RunCompletionRequest) -> RunCompletionResponse:
        cfg = settings()
        payload = body.model_dump_json().encode("utf-8")
        last_error = None
        for attempt in range(cfg.completion_callback_attempts):
            try:
                response = httpx.post(
                    self._base + f"/internal/ai/runs/{run_id}/completion",
                    headers={**self._headers, "Content-Type": "application/json"},
                    content=payload,
                    timeout=self._timeout,
                )
                response.raise_for_status()
                return RunCompletionResponse.model_validate(response.json())
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in (429, 500, 502, 503, 504):
                    raise
                last_error = exc
                if attempt + 1 < cfg.completion_callback_attempts:
                    time.sleep(min(2**attempt, 4))
            except httpx.TransportError as exc:
                last_error = exc
                if attempt + 1 < cfg.completion_callback_attempts:
                    time.sleep(min(2**attempt, 4))
        if last_error:
            raise last_error
        raise RuntimeError("완료 callback 응답을 받지 못했습니다.")
