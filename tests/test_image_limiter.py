from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest
from google.genai.errors import ClientError

from funding_story import image_limiter
from funding_story.image_limiter import ImageLimiter, MemoryStore, PostgresStore
from funding_story.infrastructure.persistence import connection
from funding_story.provider_errors import retry_metadata


@pytest.fixture
def cfg():
    return SimpleNamespace(
        image_generation_concurrency=2,
        image_request_interval_seconds=5,
        image_request_max_interval_seconds=20,
        image_retry_delay_seconds=5,
        image_retry_max_delay_seconds=30,
    )


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]
    monkeypatch.setattr(image_limiter.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(image_limiter.random, "uniform", lambda low, high: high)
    return now


def test_pacing_and_concurrency_are_shared_across_runs(cfg, clock):
    store = MemoryStore()
    first, second = ImageLimiter(cfg, store), ImageLimiter(cfg, store)
    a, _ = first.acquire()
    assert second.acquire() == (None, 5)
    clock[0] = 5
    b, _ = second.acquire()
    clock[0] = 10
    assert first.acquire()[0] is None
    first.finish(a, success=True)
    assert second.acquire()[0] is not None
    assert b is not None


def test_overload_wave_coalesces_then_success_recovers_concurrency(cfg, clock):
    store = MemoryStore()
    gate = ImageLimiter(cfg, store)
    a, _ = gate.acquire()
    clock[0] = 5
    b, _ = gate.acquire()
    gate.finish(a, throttled=True)
    gate.finish(b, throttled=True, retry_after=90)
    assert store.state["level"] == 1 and store.state["limit"] == 1
    assert gate.acquire() == (None, 90)
    clock[0] = 95
    for _ in range(3):
        p, delay = gate.acquire()
        if p is None:
            clock[0] += delay
            p, _ = gate.acquire()
        gate.finish(p, success=True)
    assert store.state["limit"] == 2 and store.state["level"] == 0
    assert 5 <= store.state["interval"] < 7.5


def test_old_inflight_success_does_not_undo_cooldown(cfg, clock):
    store = MemoryStore()
    gate = ImageLimiter(cfg, store)
    a, _ = gate.acquire()
    clock[0] = 5
    b, _ = gate.acquire()
    gate.finish(a, throttled=True, retry_after=120)
    gate.finish(b, success=True)
    assert store.state["healthy"] == 0
    assert gate.acquire() == (None, 120)


def test_long_server_minimum_survives_idle_reset(cfg, clock):
    gate = ImageLimiter(cfg, MemoryStore())
    permit, _ = gate.acquire()
    gate.finish(permit, throttled=True, retry_after=1000)
    clock[0] = 800
    assert gate.acquire() == (None, 200)


def test_active_lease_renewal_and_dead_worker_recovery(cfg, clock):
    cfg.image_generation_concurrency = 1
    gate = ImageLimiter(cfg, MemoryStore())
    permit, _ = gate.acquire()
    clock[0] = 200
    gate.renew([permit])
    clock[0] = 220
    assert gate.acquire()[0] is None
    clock[0] = 411
    assert gate.acquire()[0] is not None


def test_retry_metadata_respects_largest_hint_and_drops_sensitive_values():
    exc = ClientError(
        429,
        {
            "error": {
                "message": "quota: secret prompt https://signed-url.invalid",
                "details": [
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "45.5s"},
                    {
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": "RATE_LIMIT_EXCEEDED",
                        "metadata": {"secret": "never-log"},
                    },
                    {
                        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                        "violations": [{"subject": "secret"}],
                    },
                ],
            }
        },
        response=httpx.Response(429, headers={"Retry-After": "120"}),
    )
    assert retry_metadata(exc) == {
        "retry_after_seconds": 120,
        "category": "quota",
        "reasons": ["RATE_LIMIT_EXCEEDED"],
        "quota_metrics": [],
    }


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-5", "bad"])
def test_malformed_retry_after_does_not_break_scheduler(value):
    exc = ClientError(429, {}, response=httpx.Response(429, headers={"Retry-After": value}))
    assert retry_metadata(exc)["retry_after_seconds"] == 0


def test_http_date_retry_after(monkeypatch):
    from funding_story import provider_errors

    monkeypatch.setattr(provider_errors.time, "time", lambda: 0)
    exc = ClientError(
        429, {}, response=httpx.Response(429, headers={"Retry-After": "Thu, 01 Jan 1970 00:02:00 GMT"})
    )
    assert retry_metadata(exc)["retry_after_seconds"] == 120


def test_openai_retry_headers_and_request_id_are_preserved_without_error_body():
    exc = httpx.HTTPStatusError(
        "sensitive prompt",
        request=httpx.Request("POST", "https://api.openai.com/v1/images/generations"),
        response=httpx.Response(
            429,
            headers={"x-ratelimit-reset-requests": "2500ms", "x-request-id": "req_safe-123"},
        ),
    )
    assert retry_metadata(exc) == {
        "retry_after_seconds": 2.5,
        "category": "resource_exhausted",
        "reasons": [],
        "quota_metrics": [],
        "request_id": "req_safe-123",
    }


def test_quota_documentation_link_is_not_proof_of_quota_exhaustion():
    exc = ClientError(429, {"error": {"message": "Resource exhausted. See https://cloud.google.com/quotas"}})
    assert retry_metadata(exc)["category"] == "resource_exhausted"


def test_quota_metric_is_extracted_without_project_or_message():
    exc = ClientError(
        429,
        {
            "error": {
                "message": "Quota exceeded for aiplatform.googleapis.com/generate_content_requests_per_minute "
                "project=private-project https://private-signed-url"
            }
        },
    )
    data = retry_metadata(exc)
    assert data["category"] == "quota"
    assert data["quota_metrics"] == ["aiplatform.googleapis.com/generate_content_requests_per_minute"]
    assert "private" not in str(data)


def test_independent_postgres_clients_share_admission_and_cooldown(cfg, postgres_container):
    key = "test-image-rate"
    gates = [ImageLimiter(cfg, PostgresStore(key)) for _ in range(8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        permits = list(pool.map(lambda gate: gate.acquire()[0], gates))
    accepted = [(gate, permit) for gate, permit in zip(gates, permits, strict=True) if permit]
    assert len(accepted) == 1  # Atomic pacing is shared across worker processes.
    gate, permit = accepted[0]
    gate.finish(permit, throttled=True, retry_after=120)
    assert all(110 < candidate.acquire()[1] <= 120 for candidate in gates)
    with connection() as conn:
        state = conn.execute(
            "SELECT data FROM ai_records WHERE id=%s",
            ("image-rate:" + key,),
        ).fetchone()["data"]
    assert "prompt" not in state and "image" not in state
