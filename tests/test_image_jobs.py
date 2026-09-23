from collections import Counter
from threading import Barrier, Event
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError

from funding_story import image_jobs, provider
from funding_story.image_jobs import ImageJob, generate_images
from funding_story.image_limiter import ImageLimiter, MemoryStore


class ProviderError(Exception):
    def __init__(self, code, retry_after=None):
        self.code = code
        self.response = SimpleNamespace(headers={"Retry-After": str(retry_after)} if retry_after else {})


@pytest.fixture
def config(monkeypatch):
    cfg = SimpleNamespace(
        image_generation_concurrency=2,
        image_generation_attempts=5,
        image_retry_delay_seconds=5,
        image_retry_max_delay_seconds=30,
        image_request_interval_seconds=0.001,
        image_request_max_interval_seconds=20,
        image_generation_budget_seconds=3600,
    )
    gate = ImageLimiter(cfg, MemoryStore())
    monkeypatch.setattr(image_jobs, "settings", lambda: cfg)
    monkeypatch.setattr(image_jobs, "limiter", lambda: gate)
    return cfg


@pytest.fixture
def clock(monkeypatch):
    now = [0.0]
    # Both modules use this same clock. Thread scheduling still uses real Events.
    monkeypatch.setattr(image_jobs.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(image_jobs.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    monkeypatch.setattr(image_jobs.random, "uniform", lambda low, high: high)
    return now


def test_calls_overlap_then_retry_only_failed_slot(monkeypatch, config):
    barrier = Barrier(2)
    attempts = Counter()
    refs = [(b"reference", "image/png")]
    config.image_retry_delay_seconds = 0.001

    def generate(prompt, references, *, size):
        assert references is refs
        assert size == "1024x1024"
        attempts[prompt] += 1
        if attempts[prompt] == 1 and prompt in ("a", "b"):
            barrier.wait(timeout=5)
        if prompt == "a" and attempts[prompt] == 1:
            raise ProviderError(429)
        return prompt.encode(), "image/png"

    monkeypatch.setattr(provider, "image_once", generate)
    result, failed = generate_images([ImageJob(slot, slot) for slot in "abcd"], refs)
    assert set(result) == set("abcd") and not failed
    assert attempts == Counter(a=2, b=1, c=1, d=1)


@pytest.mark.parametrize(
    "failure,retryable",
    [
        (ProviderError(400), False),
        (ProviderError(401), False),
        (ProviderError(403), False),
        (RuntimeError("bug"), False),
        (ProviderError(429), True),
        (ProviderError(503), True),
        (httpx.ReadTimeout("timeout"), True),
        (APIConnectionError(request=object()), True),
        (provider.MissingImageError("empty"), True),
    ],
)
def test_transient_failures_are_bounded_and_never_success(monkeypatch, config, clock, failure, retryable):
    attempts = []

    def generate(*_, **__):
        attempts.append(1)
        raise failure

    monkeypatch.setattr(provider, "image_once", generate)
    assert generate_images([ImageJob("slot", "prompt")], []) == ({}, {"slot"})
    assert len(attempts) == (5 if retryable else 1)


def test_budget_accepts_inflight_success_without_claiming_missing_slots(monkeypatch, config, clock):
    config.image_generation_budget_seconds = 1
    config.image_generation_concurrency = 1

    def generate(prompt, _, **__):
        clock[0] = 2
        return b"png", "image/png"

    monkeypatch.setattr(provider, "image_once", generate)
    result, failed = generate_images([ImageJob(slot, slot) for slot in "abc"], [])
    assert set(result) == {"a"} and failed == {"b", "c"}


def test_retry_after_exceeding_budget_is_not_shortened(monkeypatch, config, clock):
    config.image_generation_budget_seconds = 10
    calls = []
    events = []
    monkeypatch.setattr(image_jobs, "emit", lambda event, **fields: events.append((event, fields)))

    def generate(*_, **__):
        calls.append(1)
        raise ProviderError(429, retry_after=120)

    monkeypatch.setattr(provider, "image_once", generate)
    assert generate_images([ImageJob("a", "a")], []) == ({}, {"a"})
    assert len(calls) == 1
    assert events[-1][0] == "image_batch_finished"
    assert events[-1][1]["budget_exhausted"] is True


def test_results_remain_associated_when_completion_order_reverses(monkeypatch, config):
    later = Event()

    def generate(prompt, _, **__):
        if prompt == "first":
            assert later.wait(timeout=5)
        else:
            later.set()
        return prompt.encode(), "image/png"

    monkeypatch.setattr(provider, "image_once", generate)
    result, failed = generate_images([ImageJob(slot, slot) for slot in ("first", "second")], [])
    assert not failed
    assert result["first"][0] == b"first" and result["second"][0] == b"second"


def test_429_gates_new_slots_and_retries_failed_slot_first(monkeypatch, config, clock):
    config.image_generation_concurrency = 1
    calls = []

    def generate(prompt, _, **__):
        calls.append((prompt, clock[0]))
        if len(calls) == 1:
            raise ProviderError(429, retry_after=40)
        return prompt.encode(), "image/png"

    monkeypatch.setattr(provider, "image_once", generate)
    result, failed = generate_images([ImageJob(slot, slot) for slot in "abc"], [])
    assert calls[0] == ("a", 0)
    assert calls[1][0] == "a" and calls[1][1] >= 40
    assert set(result) == set("abc") and not failed


def test_503_wait_does_not_stop_unrelated_ready_slots(monkeypatch, config, clock):
    config.image_generation_concurrency = 1
    calls = []

    def generate(prompt, _, **__):
        calls.append(prompt)
        if calls == ["a"]:
            raise ProviderError(503, retry_after=40)
        return prompt.encode(), "image/png"

    monkeypatch.setattr(provider, "image_once", generate)
    result, failed = generate_images([ImageJob(slot, slot) for slot in "abc"], [])
    assert calls == ["a", "b", "c", "a"]
    assert set(result) == set("abc") and not failed


def test_no_five_minute_success_shortcut(monkeypatch, config, clock):
    config.image_generation_concurrency = 1

    def generate(prompt, _, **__):
        clock[0] += 170
        return prompt.encode(), "image/png"

    monkeypatch.setattr(provider, "image_once", generate)
    result, failed = generate_images([ImageJob(slot, slot) for slot in "abc"], [])
    assert set(result) == set("abc") and not failed
    assert clock[0] > 500


def test_duplicate_slots_are_rejected():
    with pytest.raises(ValueError):
        generate_images([ImageJob("a", "a"), ImageJob("a", "b")], [])


def test_empty_batch_makes_no_calls():
    assert generate_images([], []) == ({}, set())


def test_job_specific_size_and_references_override_batch_defaults(monkeypatch, config):
    selected = ((b"selected", "image/png"),)

    def generate(prompt, references, *, size):
        assert prompt == "prompt"
        assert references is selected
        assert size == "1536x1024"
        return b"png", "image/png"

    monkeypatch.setattr(provider, "image_once", generate)
    result, failed = generate_images(
        [ImageJob("slot", "prompt", size="1536x1024", references=selected)],
        [(b"default", "image/png")],
    )
    assert result == {"slot": (b"png", "image/png")}
    assert not failed


def test_shared_limiter_failure_does_not_bypass_limit(monkeypatch, config):
    class BrokenGate:
        def acquire(self):
            raise ConnectionError("PostgreSQL unavailable")

    monkeypatch.setattr(image_jobs, "limiter", BrokenGate)
    calls = []
    monkeypatch.setattr(provider, "image_once", lambda *_, **__: calls.append(1))
    with pytest.raises(ConnectionError):
        generate_images([ImageJob("a", "a")], [])
    assert not calls
