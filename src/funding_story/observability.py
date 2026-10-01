import json
import sys
import threading
from contextlib import contextmanager
from contextvars import ContextVar

_fields = ContextVar("operational_fields", default=None)
_log_lock = threading.Lock()


@contextmanager
def event_context(**fields):
    token = _fields.set({**(_fields.get() or {}), **fields})
    try:
        yield
    finally:
        _fields.reset(token)


@contextmanager
def stage(name):
    emit("job_stage_started", stage=name)
    try:
        yield
    except Exception as exc:
        emit("job_stage_failed", stage=name, error_type=type(exc).__name__)
        raise
    else:
        emit("job_stage_finished", stage=name)


def emit(event, **fields):
    """Write a credential-safe structured event."""
    line = json.dumps({"event": event, **(_fields.get() or {}), **fields}, ensure_ascii=False)
    with _log_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
