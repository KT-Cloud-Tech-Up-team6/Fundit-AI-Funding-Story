import time
from datetime import UTC, datetime
from io import BytesIO

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


def read_source_image(reference: SourceImageRef) -> tuple[bytes, str]:
    if reference.expires_at <= datetime.now(UTC):
        raise ValueError("입력 이미지 읽기 URL이 만료되었습니다.")
    with httpx.stream(
        "GET",
        str(reference.read_url),
        timeout=settings().internal_http_timeout_seconds,
        follow_redirects=False,
    ) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type != reference.content_type:
            raise ValueError("입력 이미지 MIME이 계약과 다릅니다.")
        chunks = []
        size = 0
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > MAX_IMAGE_BYTES or size > reference.file_size:
                raise ValueError("입력 이미지 크기가 계약을 초과합니다.")
            chunks.append(chunk)
    content = b"".join(chunks)
    if len(content) != reference.file_size:
        raise ValueError("입력 이미지 크기가 계약과 다릅니다.")
    with Image.open(BytesIO(content)) as image:
        expected_format = {
            "image/png": "PNG",
            "image/jpeg": "JPEG",
            "image/webp": "WEBP",
        }[reference.content_type]
        if image.format != expected_format:
            raise ValueError("지원하지 않는 입력 이미지입니다.")
        image.verify()
    return content, content_type


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
