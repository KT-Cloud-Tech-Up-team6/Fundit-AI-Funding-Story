"""Run Funding Story work in a process that can be stopped without live model threads."""

import os
import signal
import subprocess
import sys
import threading
import time

from .bootstrap import application
from .config import settings
from .observability import emit, event_context
from .tasks import handle_job_failure
from .worker_errors import JobOwnershipLost, JobProcessError, JobTimeoutError


def stop_process(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    # The Python child may already have exited while renderer grandchildren remain.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


def run_child(command, timeout, stop, check_owner=None):
    try:
        process = subprocess.Popen(command, start_new_session=True)
    except OSError as exc:
        raise JobProcessError("Funding Story child process could not start") from exc
    deadline = time.monotonic() + timeout
    last_check = 0
    try:
        while process.poll() is None:
            if stop.is_set():
                return None
            if time.monotonic() >= deadline:
                raise JobTimeoutError("Funding Story job deadline exceeded")
            if check_owner and time.monotonic() - last_check >= 5:
                check_owner()
                last_check = time.monotonic()
            stop.wait(min(0.2, max(0, deadline - time.monotonic())))
        return process.returncode
    finally:
        stop_process(process)


def execute_isolated(record_id, stop=None):
    stop = stop or threading.Event()
    with application.claim_job(record_id) as row:
        if row is None:
            return
        with event_context(run_id=record_id, kind=row["kind"], session_id=row["data"].get("session_id")):
            cfg = settings()
            timeout = (
                cfg.completion_delivery_timeout_seconds
                if row["data"].get("_completion")
                else cfg.funding_story_job_timeout_seconds
            )
            remaining = application.job_queued_at(row) + timeout - time.time()
            emit("job_claimed", timeout_seconds=round(max(0, remaining), 3))
            try:
                if remaining <= 0:
                    raise JobTimeoutError("Funding Story job deadline exceeded")
                code = run_child(
                    [sys.executable, "-m", "funding_story.job_process", record_id, row["data"]["_lease_token"]],
                    remaining, stop,
                    lambda: application._check_owner(application.get_job(record_id), row["data"]["_lease_token"]),
                )
                if code is None:
                    # A new worker will reclaim this job after graceful lease release.
                    emit("job_interrupted", reason="worker_stopping")
                    return
                current = application.get_job(record_id)
                # A queued row with a saved payload is a scheduled delivery retry.
                if code != 0 or (
                    current["data"]["status"] == "running" and not current["data"].get("_completion")
                ):
                    raise JobProcessError("Funding Story child process did not finish its job")
            except JobOwnershipLost:
                emit("job_ownership_lost")
            except (JobTimeoutError, JobProcessError) as exc:
                emit("job_process_failed", error_type=type(exc).__name__)
                handle_job_failure(row, exc)
