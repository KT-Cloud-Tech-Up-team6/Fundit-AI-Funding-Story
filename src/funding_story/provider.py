import base64
import binascii
import time
from io import BytesIO
from pathlib import Path

from google import genai
from google.genai import types
from openai import OpenAI
from PIL import Image

from .config import settings
from .observability import emit


def _reject_automated_test_call(cfg):
    if getattr(cfg, "app_env", None) == "test":
        raise RuntimeError("APP_ENV=test에서는 외부 모델을 호출할 수 없습니다.")


def client():
    cfg = settings()
    _reject_automated_test_call(cfg)
    return genai.Client(
        vertexai=True,
        project=cfg.google_cloud_project or None,
        location=cfg.google_cloud_location,
        http_options=types.HttpOptions(timeout=180000, retry_options=types.HttpRetryOptions(attempts=1)),
    )


def eks_projected_identity_token_provider(token_file):
    """Return an OpenAI SDK provider that rereads the rotating EKS projected token."""
    if not token_file.strip():
        raise RuntimeError("OPENAI_WIF_TOKEN_FILE 설정이 필요합니다.")
    path = Path(token_file)

    def get_token():
        try:
            token = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError("EKS projected identity token을 읽을 수 없습니다.") from exc
        if not token:
            raise RuntimeError("EKS projected identity token이 비어 있습니다.")
        return token

    return {"token_type": "jwt", "get_token": get_token}


def openai_client():
    cfg = settings()
    _reject_automated_test_call(cfg)
    options = {
        "timeout": 180.0,
        # Slot scheduling owns retries so an SDK retry cannot multiply the batch policy.
        "max_retries": 0,
    }
    if cfg.openai_auth_mode == "eks_wif":
        required = {
            "OPENAI_IDENTITY_PROVIDER_ID": cfg.openai_identity_provider_id,
            "OPENAI_SERVICE_ACCOUNT_ID": cfg.openai_service_account_id,
            "OPENAI_WIF_AUDIENCE": cfg.openai_wif_audience,
            "OPENAI_WIF_TOKEN_FILE": cfg.openai_wif_token_file,
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if missing:
            raise RuntimeError(f"OpenAI WIF 설정이 필요합니다: {', '.join(missing)}")
        options["workload_identity"] = {
            "identity_provider_id": cfg.openai_identity_provider_id,
            "service_account_id": cfg.openai_service_account_id,
            "provider": eks_projected_identity_token_provider(cfg.openai_wif_token_file),
        }
    else:
        key = cfg.openai_api_key.get_secret_value() if cfg.openai_api_key else ""
        if not key.strip():
            raise RuntimeError("OPENAI_API_KEY 설정이 필요합니다.")
        options["api_key"] = key
        if cfg.openai_project:
            options["project"] = cfg.openai_project
        if cfg.openai_organization:
            options["organization"] = cfg.openai_organization
    return OpenAI(**options)


def transient_call(fn, *, sleeper=time.sleep):
    for attempt in range(3):
        try:
            return fn()
        except Exception as exc:
            code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
            if code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            delay = (15, 30)[attempt]
            emit("provider_retry", status=code, attempt=attempt + 1, delay_seconds=delay)
            sleeper(delay)


def structured(prompt, schema, references=()):
    with client() as api:
        res = transient_call(
            lambda: api.models.generate_content(
                model=settings().text_model,
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(text=prompt),
                            *[types.Part.from_bytes(data=d, mime_type=m) for d, m in references],
                        ],
                    )
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_json_schema=schema,
                    temperature=0.4,
                    max_output_tokens=24000,
                ),
            )
        )
    emit(
        "model_usage",
        model=settings().text_model,
        usage=res.usage_metadata.model_dump(mode="json") if res.usage_metadata else {},
    )
    return res.text or ""


def stream_with_retry(factory, sleeper=time.sleep):
    for attempt in range(3):
        emitted = False
        try:
            for text in factory():
                emitted = True
                yield text
            return
        except Exception as exc:
            code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
            if emitted or code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            delay = (15, 30)[attempt]
            emit("stream_retry", status=code, attempt=attempt + 1, delay_seconds=delay)
            sleeper(delay)


def chat_stream(prompt):
    def chunks():
        with client() as api:
            for chunk in api.models.generate_content_stream(
                model=settings().text_model,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=0.4, max_output_tokens=3000),
            ):
                if chunk.text:
                    yield chunk.text

    yield from stream_with_retry(chunks)


class MissingImageError(ValueError):
    """A model response without usable image content can be retried by the slot scheduler."""


def image(prompt, references):
    # Compatibility for standalone callers; generation workers own retries at the slot level.
    return transient_call(lambda: image_once(prompt, references))


def _validated_image(content, expected_mime=None):
    if not content:
        raise MissingImageError("모델이 빈 이미지를 반환했습니다.")
    try:
        with Image.open(BytesIO(content)) as image:
            mime = Image.MIME.get(image.format)
            if mime not in ("image/png", "image/jpeg", "image/webp"):
                raise MissingImageError("모델이 지원하지 않는 이미지 형식을 반환했습니다.")
            if expected_mime and mime != expected_mime:
                raise MissingImageError("생성 이미지 MIME이 실제 형식과 다릅니다.")
            image.verify()
    except (OSError, ValueError) as exc:
        raise MissingImageError("모델이 올바른 이미지를 반환하지 않았습니다.") from exc
    return content, mime


def _google_image_once(prompt, references):
    parts = [
        types.Part.from_text(
            text="Generate an image, not a text response. Use the supplied reference product.\n" + prompt
        )
    ] + [types.Part.from_bytes(data=data, mime_type=mime) for data, mime in references]
    with client() as api:
        res = api.models.generate_content(
            model=settings().image_model,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
        )
    usage = getattr(res, "usage_metadata", None)
    if usage:
        emit(
            "image_usage",
            **{
                name: getattr(usage, name, None)
                for name in (
                    "prompt_token_count",
                    "candidates_token_count",
                    "total_token_count",
                    "cached_content_token_count",
                )
            },
        )
    for candidate in res.candidates or []:
        if not candidate.content:
            continue
        for part in candidate.content.parts or []:
            if (
                part.inline_data
                and part.inline_data.data
                and part.inline_data.mime_type in ("image/png", "image/jpeg", "image/webp")
            ):
                return _validated_image(part.inline_data.data, part.inline_data.mime_type)
    emit(
        "image_response_missing",
        finishes=[str(c.finish_reason) for c in res.candidates or []],
        feedback=str(res.prompt_feedback),
    )
    raise MissingImageError("모델이 이미지를 반환하지 않았습니다.")


def _reference_file(content, mime, index):
    suffix = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(mime)
    if not suffix:
        raise ValueError("지원하지 않는 참조 이미지 MIME입니다.")
    file = BytesIO(content)
    file.name = f"reference-{index}.{suffix}"
    return file


def _openai_image_once(prompt, references, size):
    if len(references) > 16:
        raise ValueError("OpenAI 이미지 편집 참조는 슬롯당 최대 16개입니다.")
    cfg = settings()
    request = {
        "model": cfg.image_model,
        "prompt": prompt,
        "n": 1,
        "quality": cfg.image_quality,
        "size": size,
        "output_format": "png",
    }
    with openai_client() as api:
        if references:
            response = api.images.edit(
                image=[
                    _reference_file(content, mime, index)
                    for index, (content, mime) in enumerate(references)
                ],
                **request,
            )
        else:
            response = api.images.generate(**request)
    item = response.data[0] if response.data else None
    encoded = getattr(item, "b64_json", None)
    if not encoded:
        raise MissingImageError("OpenAI가 이미지를 반환하지 않았습니다.")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MissingImageError("OpenAI 이미지 인코딩이 올바르지 않습니다.") from exc
    usage = getattr(response, "usage", None)
    emit(
        "image_usage",
        provider="openai",
        model=cfg.image_model,
        request_id=getattr(response, "_request_id", None),
        usage=usage.model_dump(mode="json") if hasattr(usage, "model_dump") else {},
    )
    return _validated_image(content, "image/png")


def image_once(prompt, references, *, size="1024x1024"):
    cfg = settings()
    if cfg.image_provider == "openai":
        return _openai_image_once(prompt, references, size)
    return _google_image_once(prompt, references)
