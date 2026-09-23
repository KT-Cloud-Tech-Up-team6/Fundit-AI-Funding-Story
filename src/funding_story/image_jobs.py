"""Paced image calls; retry failed slots without replaying successful generations."""

import random
import time
from collections import Counter, deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass

import httpx
from openai import APIConnectionError

from . import provider
from .config import settings
from .image_limiter import limiter
from .observability import emit
from .provider_errors import retry_metadata, status_code


@dataclass(frozen=True)
class ImageJob:
    slot_id: str
    prompt: str
    size: str = "1024x1024"
    references: tuple[tuple[bytes, str], ...] | None = None


def _retryable(exc):
    return status_code(exc) in (429, 500, 502, 503, 504) or isinstance(
        exc, (httpx.TransportError, APIConnectionError, TimeoutError, provider.MissingImageError)
    )


def generate_images(jobs: list[ImageJob], references):
    if len({job.slot_id for job in jobs}) != len(jobs):
        raise ValueError("이미지 슬롯 ID는 중복될 수 없습니다.")
    if not jobs:
        return {}, set()
    cfg, gate = settings(), limiter()
    deadline = time.monotonic() + cfg.image_generation_budget_seconds
    pending = deque(jobs)
    retries = deque()
    ready_at = {}
    attempts = Counter()
    results = {}
    failed = {job.slot_id for job in jobs}
    permits = {}
    renewed_at = time.monotonic()

    def execute(job, permit):
        start = time.monotonic()
        try:
            selected_references = references if job.references is None else job.references
            result = provider.image_once(job.prompt, selected_references, size=job.size)
        except Exception as exc:  # noqa: BLE001 -- Never retain errors containing user input.
            code, meta = status_code(exc), retry_metadata(exc)
            state = gate.finish(permit, throttled=code == 429, retry_after=meta["retry_after_seconds"])
            if code == 429:
                emit("image_rate_adjusted", cause="429", **state)
            emit(
                "image_attempt",
                slot_id=job.slot_id,
                attempt=attempts[job.slot_id],
                seconds=round(time.monotonic() - start, 3),
                status=code,
                outcome="failed",
                **meta,
            )
            return None, _retryable(exc), code, meta["retry_after_seconds"]
        else:
            state = gate.finish(permit, success=True)
            emit("image_rate_adjusted", cause="success", **state)
            emit(
                "image_attempt",
                slot_id=job.slot_id,
                attempt=attempts[job.slot_id],
                seconds=round(time.monotonic() - start, 3),
                outcome="succeeded",
            )
            return result, False, None, 0

    with ThreadPoolExecutor(
        max_workers=cfg.image_generation_concurrency, thread_name_prefix="story-image"
    ) as pool:
        active = {}
        while pending or retries or active:
            now = time.monotonic()
            if active and now - renewed_at >= 30:
                gate.renew(list(permits.values()))
                renewed_at = now
            ready = next((job for job in retries if ready_at[job.slot_id] <= now), None)
            candidate = ready or (pending[0] if pending else None)
            pause = min((max(0, t - now) for t in ready_at.values()), default=0.1)
            if candidate and len(active) < cfg.image_generation_concurrency and now < deadline:
                permit, pause = gate.acquire()
                if permit:
                    if ready:
                        retries.remove(ready)
                        ready_at.pop(ready.slot_id)
                    else:
                        pending.popleft()
                    attempts[candidate.slot_id] += 1
                    permits[candidate.slot_id] = permit
                    try:
                        active[pool.submit(execute, candidate, permit)] = candidate
                    except BaseException:
                        gate.finish(permit)
                        raise
                    continue
            if not active:
                if now >= deadline or now + pause >= deadline:
                    break
                time.sleep(max(0.001, min(pause, 1)))
                continue
            done, _ = wait(active, timeout=max(0.001, min(pause, 0.1)), return_when=FIRST_COMPLETED)
            for future in done:
                job = active.pop(future)
                permits.pop(job.slot_id)
                result, retryable, code, server_delay = future.result()
                if result is not None:
                    results[job.slot_id] = result
                    failed.remove(job.slot_id)
                elif retryable and attempts[job.slot_id] < cfg.image_generation_attempts:
                    # 429 gates the whole model; other transient failures delay only this slot.
                    base = min(
                        cfg.image_retry_max_delay_seconds,
                        cfg.image_retry_delay_seconds * 2 ** (attempts[job.slot_id] - 1),
                    )
                    delay = 0 if code == 429 else max(server_delay, random.uniform(base / 2, base))
                    ready_at[job.slot_id] = time.monotonic() + delay
                    retries.append(job)
    emit(
        "image_batch_finished",
        expected=len(jobs),
        completed=len(results),
        failed=len(failed),
        attempts=sum(attempts.values()),
        budget_exhausted=bool(pending or retries) or time.monotonic() >= deadline,
    )
    return results, failed
