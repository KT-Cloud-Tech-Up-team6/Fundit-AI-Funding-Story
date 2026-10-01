import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from funding_story import isolated_job, media, tasks, worker
from funding_story.application import FundingStoryApplication
from funding_story.infrastructure.persistence import repository
from funding_story.infrastructure.ttl_state import InMemoryTtlRecordRepository, PostgresTtlRecordRepository
from funding_story.models import RunCompletionResponse
from funding_story.observability import emit, event_context
from funding_story.worker_errors import JobOwnershipLost, JobTimeoutError


def jobs(monkeypatch, records=None):
    records = records or InMemoryTtlRecordRepository()
    app = FundingStoryApplication(records)
    session_id = records.create("local-test", "session", {})
    run_id = records.create("local-test", "run", {
        "status": "queued", "session_id": session_id, "confirmed_revision": 1,
        "_queued_at": time.time(), "error": None,
    })
    records.save(session_id, {"active_run_id": run_id})
    monkeypatch.setattr(tasks, "application", app)
    monkeypatch.setattr(isolated_job, "application", app)
    return app, records, run_id, session_id


def test_real_hung_process_is_killed_before_it_can_produce_late_output(tmp_path):
    late_output = tmp_path / "must-not-exist"
    command = [sys.executable, "-c", (
        "import subprocess,sys,time; "
        "subprocess.Popen([sys.executable,'-c',sys.argv[1]]); time.sleep(10)"
    ), f"import time; from pathlib import Path; time.sleep(0.5); Path({str(late_output)!r}).touch()"]
    with pytest.raises(JobTimeoutError):
        isolated_job.run_child(command, 0.1, threading.Event())
    time.sleep(0.55)
    assert not late_output.exists()


def test_timeout_sends_failed_callback_and_releases_active_run(monkeypatch, capsys):
    app, records, run_id, session_id = jobs(monkeypatch)
    callbacks = []

    class Backend:
        def complete(self, rid, body):
            callbacks.append(body)
            return RunCompletionResponse(run_id=rid, status=body.status)

    monkeypatch.setattr(tasks, "BackendClient", lambda _: Backend())
    monkeypatch.setattr(isolated_job, "run_child", lambda *args: (_ for _ in ()).throw(JobTimeoutError()))
    isolated_job.execute_isolated(run_id)
    assert callbacks[0].status == "failed"
    assert callbacks[0].error.code == "GENERATION_TIMEOUT"
    assert records.get(run_id)["data"]["status"] == "failed"
    assert records.get(session_id)["data"]["active_run_id"] is None
    assert "_lease_token" not in records.get(run_id)["data"]
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert all(row["run_id"] == run_id for row in events)
    assert app.pending_job_ids() == []


def test_queue_wait_is_included_in_timeout_and_expired_work_does_not_start(monkeypatch):
    _, records, run_id, _ = jobs(monkeypatch)
    row = records.get(run_id)
    row["data"]["_queued_at"] = time.time() - 2000
    records.save(run_id, row["data"])
    called = []
    monkeypatch.setattr(isolated_job, "run_child", lambda *args: pytest.fail("expired generation started"))
    monkeypatch.setattr(tasks, "handle_job_failure", lambda *args: called.append(args))
    monkeypatch.setattr(isolated_job, "handle_job_failure", lambda *args: called.append(args))
    isolated_job.execute_isolated(run_id)
    assert len(called) == 1 and isinstance(called[0][1], JobTimeoutError)


def test_restart_replays_saved_callback_without_repeating_generation(monkeypatch, capsys):
    app, records, run_id, _ = jobs(monkeypatch)
    cfg = tasks.settings().model_copy(update={
        "internal_api_key": "do-not-log", "project_service_base_url": "https://local.example",
        "completion_callback_attempts": 3,
    })
    monkeypatch.setattr(media, "settings", lambda: cfg)
    monkeypatch.setattr(media.time, "sleep", lambda _: None)
    payloads, model_calls = [], []
    body = tasks._failed_completion("local failure")

    def receive(request):
        payloads.append(request.content)
        if len(payloads) <= 3:
            raise httpx.ReadTimeout("do-not-log", request=request)
        return httpx.Response(200, json={"run_id": run_id, "status": "failed"})

    def generate(row):
        model_calls.append(row["id"])
        tasks._deliver_completion(media.BackendClient(row["project_id"]), run_id, body)

    monkeypatch.setattr(tasks, "generate", generate)
    with httpx.Client(transport=httpx.MockTransport(receive)) as client:
        monkeypatch.setattr(media.httpx, "post", client.post)
        tasks.execute(run_id)
        stored = records.get(run_id)["data"]
        assert stored["status"] == "queued" and stored["_completion"]["status"] == "failed"
        assert app.pending_job_ids() == []  # Retry backoff is persisted too.
        stored["_retry_after"] = time.time() - 1
        records.save(run_id, stored)
        monkeypatch.setattr(tasks, "application", FundingStoryApplication(records))
        tasks.execute(run_id)
    assert model_calls == [run_id]
    assert len(payloads) == 4 and len(set(payloads)) == 1
    assert records.get(run_id)["data"]["status"] == "failed"
    assert "_completion" not in records.get(run_id)["data"]
    log = capsys.readouterr().out
    assert "do-not-log" not in log and "local failure" not in log
    responses = [json.loads(line) for line in log.splitlines() if '"completion_callback_response"' in line]
    assert responses[-1]["http_status"] == 200 and responses[-1]["run_id"] == run_id


def test_timeout_during_delivery_preserves_successful_result(monkeypatch):
    from test_completion_delivery import completion

    app, records, run_id, _ = jobs(monkeypatch)
    original = completion("succeeded")
    app.prepare_run_delivery(run_id, original.model_dump(mode="json"))
    delivered = []

    class Backend:
        def complete(self, rid, body):
            delivered.append(body.model_dump_json())
            return RunCompletionResponse(run_id=rid, status=body.status)

    monkeypatch.setattr(tasks, "BackendClient", lambda _: Backend())
    monkeypatch.setattr(isolated_job, "run_child", lambda *args: (_ for _ in ()).throw(JobTimeoutError()))
    isolated_job.execute_isolated(run_id)
    assert delivered == [original.model_dump_json()]
    assert records.get(run_id)["data"]["status"] == "succeeded"


def test_database_poll_and_cleanup_errors_do_not_stop_worker(monkeypatch):
    stop = threading.Event()
    calls, events = [], []
    monkeypatch.setattr(worker, "open_pools", lambda: None)
    monkeypatch.setattr(worker, "close_pools", lambda: None)
    monkeypatch.setattr(worker, "emit", lambda event, **fields: events.append(event))

    def pending(lane):
        calls.append(lane)
        if len(calls) == 1:
            raise RuntimeError("database temporarily unavailable")
        stop.set()
        return []

    monkeypatch.setattr(worker, "pending_jobs", pending)
    monkeypatch.setattr(worker.application, "cleanup_expired", lambda: (_ for _ in ()).throw(RuntimeError()))
    # Make retry wait deterministic, while preserving the real stop event.
    monkeypatch.setattr(stop, "wait", lambda timeout: stop.is_set())
    worker.run_worker("funding-story", 1, 0.01, stop)
    assert len(calls) == 2
    assert "worker_poll_failed" in events and "worker_cleanup_failed" in events
    assert "worker_started" in events and "worker_stopped" in events


def test_discarded_callback_is_terminal_and_not_retried(monkeypatch):
    _, records, run_id, _ = jobs(monkeypatch)
    app = tasks.application
    monkeypatch.setattr(tasks, "generate", lambda row: app.complete_run_delivery(run_id, "discarded"))
    tasks.execute(run_id)
    assert RunCompletionResponse(run_id=run_id, status="discarded").status == "discarded"
    assert records.get(run_id)["data"]["status"] == "discarded"
    assert app.pending_job_ids() == []


def test_context_is_reset_between_jobs_and_does_not_leak_input(capsys):
    with event_context(run_id="first"):
        emit("model_usage", total_tokens=10)
    emit("worker_heartbeat")
    first, second = map(json.loads, capsys.readouterr().out.splitlines())
    assert first["run_id"] == "first" and "run_id" not in second


def test_parallel_log_events_remain_separate_json_lines(capsys):
    def log(index):
        with event_context(run_id=str(index)):
            emit("image_attempt", slot_id="hero")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(log, range(100)))
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(rows) == 100 and {row["run_id"] for row in rows} == {str(index) for index in range(100)}


def test_stale_worker_cannot_send_callback_or_change_new_owners_state(monkeypatch):
    app, records, run_id, _ = jobs(monkeypatch)
    row = records.get(run_id)
    row["data"].update(status="running", _lease_token="new-owner", _lease_until=time.time() + 90)
    records.save(run_id, row["data"])

    class Backend:
        def complete(self, *args):
            pytest.fail("stale worker sent callback")

    with pytest.raises(JobOwnershipLost):
        tasks._deliver_completion(Backend(), run_id, tasks._failed_completion("local"), lease_token="old-owner")
    with pytest.raises(JobOwnershipLost):
        app.retry_run_delivery(run_id, lease_token="old-owner")
    assert records.get(run_id)["data"] == row["data"]


def test_delivery_retry_has_a_terminal_deadline(monkeypatch):
    app, records, run_id, session_id = jobs(monkeypatch)
    app.prepare_run_delivery(run_id, tasks._failed_completion("local").model_dump(mode="json"))
    row = records.get(run_id)
    row["data"]["_queued_at"] = time.time() - 2000
    records.save(run_id, row["data"])

    class Backend:
        def complete(self, *args):
            raise httpx.ReadTimeout("local-only")

    monkeypatch.setattr(tasks, "BackendClient", lambda _: Backend())
    isolated_job.execute_isolated(run_id)
    assert records.get(run_id)["data"]["status"] == "failed"
    assert "_completion" not in records.get(run_id)["data"]
    assert records.get(session_id)["data"]["active_run_id"] is None
    assert app.pending_job_ids() == []


def test_real_supervisor_and_child_deliver_from_postgres(postgres_container, monkeypatch):
    records = PostgresTtlRecordRepository(repository(), 86400)
    app, records, run_id, session_id = jobs(monkeypatch, records)
    app.prepare_run_delivery(run_id, tasks._failed_completion("test-only").model_dump(mode="json"))
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"run_id": run_id, "status": "failed"}).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("MODEL_PROFILE", "local_google_experiment")
    monkeypatch.setenv("INTERNAL_API_KEY", "test-only")
    monkeypatch.setenv("PROJECT_SERVICE_BASE_URL", f"http://127.0.0.1:{server.server_port}")
    try:
        isolated_job.execute_isolated(run_id)
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(2)
    assert len(received) == 1 and received[0]["status"] == "failed"
    assert records.get(run_id)["data"]["status"] == "failed"
    assert "_completion" not in records.get(run_id)["data"]
    assert records.get(session_id)["data"]["active_run_id"] is None
