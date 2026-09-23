"""Read retry hints without retaining/logging error bodies, prompts or URLs."""

import math
import re
import time
from email.utils import parsedate_to_datetime


def status_code(exc):
    raw = (
        getattr(exc, "status_code", None)
        or getattr(exc, "code", None)
        or getattr(getattr(exc, "response", None), "status_code", None)
    )
    try:
        return int(raw) if raw is not None else None
    except (ValueError, TypeError):
        return None


def _seconds(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (ValueError, TypeError):
        return None


def _duration(value):
    if value is None:
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(ms|s)?\s*", str(value), re.IGNORECASE)
    if not match:
        return None
    seconds = float(match.group(1))
    return seconds / 1000 if (match.group(2) or "s").lower() == "ms" else seconds


def retry_metadata(exc):
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) or {}
    raw = headers.get("Retry-After") or headers.get("retry-after")
    delays = []
    if raw is not None:
        seconds = _seconds(raw)
        if seconds is None:
            try:
                seconds = max(0, parsedate_to_datetime(raw).timestamp() - time.time())
            except (ValueError, TypeError, OverflowError):
                pass
        if seconds is not None:
            delays.append(seconds)
    for name in (
        "x-ratelimit-reset-requests",
        "x-ratelimit-reset-images",
    ):
        seconds = _duration(headers.get(name))
        if seconds is not None:
            delays.append(seconds)
    payload = getattr(exc, "details", None)
    if isinstance(payload, dict):
        payload = payload.get("error", payload)
        details = payload.get("details", []) if isinstance(payload, dict) else []
    else:
        details = payload if isinstance(payload, list) else []
    reasons = []
    quota = False
    for detail in details if isinstance(details, list) else []:
        if not isinstance(detail, dict):
            continue
        kind = str(detail.get("@type", "")).rsplit("/", 1)[-1]
        if kind == "google.rpc.RetryInfo":
            value = detail.get("retryDelay")
            if isinstance(value, str) and value.endswith("s"):
                seconds = _seconds(value[:-1])
            elif isinstance(value, dict):
                sec, nanos = _seconds(value.get("seconds", 0)), _seconds(value.get("nanos", 0))
                seconds = sec + nanos / 1e9 if sec is not None and nanos is not None else None
            else:
                seconds = None
            if seconds is not None:
                delays.append(seconds)
        elif kind == "google.rpc.QuotaFailure":
            quota = True
        elif kind == "google.rpc.ErrorInfo":
            reason = detail.get("reason", "")
            if isinstance(reason, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", reason):
                reasons.append(reason)
    message = str(getattr(exc, "message", "") or "").lower()
    metrics = sorted(set(re.findall(r"aiplatform\.googleapis\.com/[a-z_]+", message)))
    quota = (
        quota
        or bool(metrics)
        or bool(
            re.search(
                r"quota(?: limit)? (?:exceeded|exhausted)|exceeded[^\n]{0,40}quota|quota metric", message
            )
        )
    )
    category = (
        "quota"
        if quota
        else "capacity"
        if "capacity" in message or "overload" in message or "high demand" in message
        else "resource_exhausted"
        if status_code(exc) == 429
        else "transient_or_other"
    )
    metadata = {
        "retry_after_seconds": max(delays, default=0),
        "category": category,
        "reasons": reasons,
        "quota_metrics": metrics,
    }
    request_id = headers.get("x-request-id") or getattr(exc, "request_id", None)
    if isinstance(request_id, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", request_id):
        metadata["request_id"] = request_id
    return metadata
