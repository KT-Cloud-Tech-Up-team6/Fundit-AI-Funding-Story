import threading
import time
from contextlib import contextmanager
from uuid import uuid4

from .domain.repositories import RecordRepository
from .observability import emit

LEASE_SECONDS = 90
RENEW_INTERVAL_SECONDS = 30


def _claim(records: RecordRepository, record_id: str, queued: str, running: str):
    token = uuid4().hex
    now = time.time()
    with records.transaction() as tx:
        row = tx.get(record_id, lock=True)
        status = row["data"].get("status")
        active_lease = float(row["data"].get("_lease_until", 0)) > now
        if status not in (queued, running) or (status == running and active_lease):
            return None
        row["data"].update(
            status=running,
            _lease_token=token,
            _lease_until=now + LEASE_SECONDS,
        )
        tx.save(record_id, row["data"])
    return row, token


def _renew(records: RecordRepository, record_id: str, token: str) -> bool:
    with records.transaction() as tx:
        row = tx.get(record_id, lock=True)
        if row["data"].get("_lease_token") != token:
            return False
        row["data"]["_lease_until"] = time.time() + LEASE_SECONDS
        tx.save(record_id, row["data"])
    return True


def _release(records: RecordRepository, record_id: str, token: str) -> None:
    with records.transaction() as tx:
        try:
            row = tx.get(record_id, lock=True)
        except LookupError:
            return
        if row["data"].get("_lease_token") != token:
            return
        row["data"].pop("_lease_token", None)
        row["data"].pop("_lease_until", None)
        tx.save(record_id, row["data"])


@contextmanager
def leased_record(records: RecordRepository, record_id: str, *, queued: str, running: str):
    claimed = _claim(records, record_id, queued, running)
    if claimed is None:
        yield None
        return

    row, token = claimed
    stopped = threading.Event()

    def heartbeat():
        while not stopped.wait(RENEW_INTERVAL_SECONDS):
            try:
                if not _renew(records, record_id, token):
                    return
            except Exception as exc:  # noqa: BLE001 - retry until the lease can be renewed
                emit(
                    "job_lease_renewal_failed",
                    record_id=record_id,
                    error_type=type(exc).__name__,
                )
                continue

    thread = threading.Thread(target=heartbeat, name=f"job-lease-{record_id}", daemon=True)
    thread.start()
    try:
        yield row
    finally:
        stopped.set()
        thread.join(timeout=1)
        _release(records, record_id, token)
