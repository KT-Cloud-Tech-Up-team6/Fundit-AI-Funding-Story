import json
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

from funding_story import media, tasks
from funding_story.models import RunCompletionRequest

RUN_ID = "11111111-1111-4111-8111-111111111111"


def completion(status):
    if status == "failed":
        return tasks._failed_completion("생성 실패")
    return RunCompletionRequest.model_validate(
        {
            "status": status,
            "generated_body": {
                "cover_image_slot_id": "hero",
                "intro_content": [{"type": "IMAGE", "slot_id": "hero"}],
            },
            "successful_images": [
                {
                    "slot_id": "hero",
                    "file_url": "https://example.com/hero.png",
                    "content_type": "image/png",
                    "file_size": 10,
                    "width": 860,
                    "height": 1200,
                }
            ],
            "failed_slots": [tasks._slot_error("benefit", "generation", "IMAGE_GENERATION_FAILED", "실패")]
            if status == "partially_succeeded"
            else [],
            "error": None,
        }
    )


@pytest.mark.parametrize("status", ["succeeded", "partially_succeeded", "failed"])
@pytest.mark.parametrize("failure", ["recover", "exhaust", "conflict", "bookkeeping"])
def test_finalized_callback_is_never_replaced(monkeypatch, status, failure):
    payloads, failures, delivered = [], [], []
    body = completion(status)
    original_payload = body.model_dump_json().encode("utf-8")
    monkeypatch.setattr(
        media,
        "settings",
        lambda: SimpleNamespace(
            internal_api_key="test-only",
            project_service_base_url="https://backend.example.com",
            internal_http_timeout_seconds=1,
            completion_callback_attempts=3,
        ),
    )
    monkeypatch.setattr(media.time, "sleep", lambda _: None)

    def receive(request):
        payloads.append(request.content)
        assert request.headers["content-type"] == "application/json"
        # Even if the original object changes, retries must keep the first wire payload.
        body.successful_images.clear()
        if failure == "conflict":
            return httpx.Response(409)
        if failure == "exhaust" or (failure == "recover" and len(payloads) == 1):
            # Simulate BE accepting the request but the response being lost.
            raise httpx.ReadTimeout("response lost", request=request)
        if failure == "recover" and len(payloads) == 2:
            return httpx.Response(503)
        return httpx.Response(200, json={"run_id": RUN_ID, "status": json.loads(request.content)["status"]})

    class Application:
        @contextmanager
        def claim_job(self, record_id):
            yield {"id": record_id, "project_id": "project-1", "kind": "run"}

        def complete_run_delivery(self, run_id, result_status):
            if failure == "bookkeeping":
                raise RuntimeError("local state unavailable")
            delivered.append(result_status)

        def fail_job(self, run_id, exc):
            failures.append(exc)

    monkeypatch.setattr(tasks, "application", Application())
    monkeypatch.setattr(tasks, "emit", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        tasks,
        "generate",
        lambda row: tasks._deliver_completion(
            media.BackendClient(row["project_id"]),
            row["id"],
            body,
        ),
    )
    with httpx.Client(transport=httpx.MockTransport(receive)) as client:
        monkeypatch.setattr(media.httpx, "post", client.post)
        tasks.execute.run(RUN_ID)

    assert len(payloads) == (3 if failure in ("recover", "exhaust") else 1)
    assert all(payload == original_payload for payload in payloads)
    assert delivered == ([status] if failure == "recover" else [])
    assert len(failures) == (0 if failure == "recover" else 1)
    assert all(isinstance(exc, tasks.CompletionDeliveryError) for exc in failures)
