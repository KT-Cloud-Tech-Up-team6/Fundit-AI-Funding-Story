import time

from google import genai
from google.genai import types
from langsmith import traceable

from .config import settings
from .observability import emit


def client():
    cfg = settings()
    return genai.Client(
        vertexai=True,
        project=cfg.google_cloud_project or None,
        location=cfg.google_cloud_location,
        http_options=types.HttpOptions(timeout=180000, retry_options=types.HttpRetryOptions(attempts=1)),
    )


def transient_call(fn, *, sleeper=time.sleep):
    for attempt in range(3):
        try:
            return fn()
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code not in (429, 500, 502, 503, 504) or attempt == 2:
                raise
            delay = (15, 30)[attempt]
            emit("provider_retry", status=code, attempt=attempt + 1, delay_seconds=delay)
            sleeper(delay)


@traceable(name="model.structured", run_type="llm", process_inputs=lambda _: {})
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
            code = getattr(exc, "code", None)
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


@traceable(
    name="model.image", process_inputs=lambda _: {}, process_outputs=lambda _: {"image": "stored separately"}
)
def image(prompt, references):
    parts = [
        types.Part.from_text(
            text="Generate an image, not a text response. Use the supplied reference product.\n" + prompt
        )
    ] + [types.Part.from_bytes(data=data, mime_type=mime) for data, mime in references]
    with client() as api:
        res = transient_call(
            lambda: api.models.generate_content(
                model=settings().image_model,
                contents=[types.Content(role="user", parts=parts)],
                config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
            )
        )
    for candidate in res.candidates or []:
        for part in candidate.content.parts or []:
            if part.inline_data:
                return part.inline_data.data, part.inline_data.mime_type
    emit(
        "image_response_missing",
        finishes=[str(c.finish_reason) for c in res.candidates or []],
        feedback=str(res.prompt_feedback),
    )
    raise ValueError("모델이 이미지를 반환하지 않았습니다.")
