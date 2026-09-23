"""Measure tasks.generate using real models/renderer and a loopback BE receiver.

Persist metadata only; input/output images and completion bodies stay in memory.
Queue delay and deployed BE/storage network latency are outside this measurement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import subprocess
import threading
import time
import uuid
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from funding_story.models import (
    FundingStoryContext,
    ProjectInput,
    Review,
    RunCompletionRequest,
    SourceImageRef,
    UploadTargetsRequest,
)
from funding_story.planner import plan

ROOT = Path(__file__).resolve().parents[1]


def sample(strength_count: int = 3) -> tuple[ProjectInput, Review, list[tuple[bytes, str]]]:
    raw = json.loads((ROOT / "tests/fixtures/appliance.json").read_text())
    project = ProjectInput.model_validate(
        {
            **raw,
            "source_images": [],
            "tone": "간결하고 신뢰감 있는 설명",
            "brand_color": "#4D8FFF",
        }
    )
    review = Review.model_validate(
        {
            "reply": "확인했습니다.",
            "product": "가볍게 꺼내 쓰고 정리하기 쉬운 무선 청소기",
            "story": "일상에서 자주 생기는 먼지를 바로 정리하는 사용 경험",
            "strengths": [
                {
                    "id": "wireless",
                    "title": "무선 이동성",
                    "description": "전원선 없이 필요한 공간으로 이동해 사용할 수 있습니다.",
                },
                {
                    "id": "slim",
                    "title": "슬림한 보관",
                    "description": "좁은 공간에도 정리하기 쉬운 형태입니다.",
                },
                {
                    "id": "handling",
                    "title": "간편한 조작",
                    "description": "한 손으로 들고 원하는 곳을 청소하기 편합니다.",
                },
            ],
            "problems": [
                {"heading": "식탁 주변", "body": "식사 후 작은 부스러기를 바로 치우기 번거롭습니다."},
                {"heading": "소파 틈새", "body": "좁은 틈에 쌓인 먼지를 청소하기 어렵습니다."},
                {"heading": "방 사이 이동", "body": "전원선을 옮겨 꽂는 과정이 번거롭습니다."},
                {"heading": "청소기 보관", "body": "사용 후 청소기를 둘 공간이 마땅하지 않습니다."},
            ],
            "missing": [],
            "tone": "간결하고 신뢰감 있는 설명",
            "brand_color": "#4D8FFF",
        }
    )
    if strength_count == 12:
        # Largest legal layout: 12 two-image feature blocks and three rewards.
        base = review.strengths[0]
        review.strengths = [base.model_copy(update={"id": f"wireless-{i}"}) for i in range(12)]
    reference = (ROOT / "tests/fixtures/original.png").read_bytes()
    return project, review, [(reference, "image/png")]


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


@contextmanager
def local_backend(reference):
    state = SimpleNamespace(descriptors={}, uploaded={}, completion=None, delivered=None)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, code, body, content_type="application/json"):
            blob = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

        def do_GET(self):
            self.reply(200, reference, "image/png")

        def do_PUT(self):
            slot = self.path.removeprefix("/upload/")
            blob = self.rfile.read(int(self.headers["Content-Length"]))
            try:
                assert len(blob) == state.descriptors[slot].file_size
                with Image.open(BytesIO(blob)) as image:
                    assert image.format == "PNG"
                    size = image.size
                    image.verify()
                state.uploaded[slot] = {"file_size": len(blob), "size": size}
                self.reply(200, {})
            except (AssertionError, KeyError, ValueError, OSError):
                self.reply(422, {"error": "invalid PNG upload"})

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            try:
                if self.path == "/internal/ai/media/upload-targets":
                    body = UploadTargetsRequest.model_validate_json(raw)
                    state.descriptors = {item.slot_id: item for item in body.outputs}
                    self.reply(
                        200,
                        {
                            "targets": [
                                {
                                    "slot_id": item.slot_id,
                                    "upload_url": state.base + "/upload/" + item.slot_id,
                                    "file_url": state.base + "/files/" + item.slot_id + ".png",
                                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                                }
                                for item in body.outputs
                            ]
                        },
                    )
                else:
                    body = RunCompletionRequest.model_validate_json(raw)
                    for image in body.successful_images:
                        uploaded = state.uploaded[image.slot_id]
                        assert uploaded["file_size"] == image.file_size
                        assert uploaded["size"] == (image.width, image.height)
                    state.completion = body
                    self.reply(200, {"run_id": self.path.split("/")[-2], "status": body.status})
            except (AssertionError, KeyError, ValueError):
                self.reply(422, {"error": "invalid completion or upload descriptor"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.base = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def run_once(sequence, strength_count, record):
    from funding_story import media, provider, tasks
    from funding_story.config import settings

    project, review, references = sample(strength_count)
    expected_scene, _ = plan(project, review)
    run_id = str(uuid.uuid4())
    stages, calls, retries = {}, [], []
    result = {
        "run": sequence,
        "strengths": strength_count,
        "started_at": datetime.now(UTC).isoformat(),
        "expected_blocks": len(expected_scene["blocks"]),
        "expected_image_slots": sum(
            n["kind"] == "image" for b in expected_scene["blocks"] for n in b["nodes"]
        ),
    }

    def timed(name, fn, *, call=False):
        def invoke(*args, **kwargs):
            begin = time.perf_counter()
            error = None
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                error = type(exc).__name__
                raise
            finally:
                elapsed = round(time.perf_counter() - begin, 3)
                if call:
                    event = {"stage": name, "seconds": elapsed, "error_type": error}
                    calls.append(event)
                    record({"event": "model_call", "run": sequence, **event})
                else:
                    stages[name] = elapsed
                    record({"event": "stage", "run": sequence, "stage": name, "seconds": elapsed})

        return invoke

    def provider_event(event, **fields):
        if event == "provider_retry":
            retries.append(fields)
            record({"event": event, "run": sequence, **fields})

    with local_backend(references[0][0]) as backend, ExitStack() as stack:
        context = FundingStoryContext(
            project={
                "business_type": project.business_type,
                "category": {"major": "테크·가전", "minor": "생활가전"},
                "title": project.title,
                "goal_amount": project.goal_amount,
            },
            rewards=project.rewards,
            source_images=[
                SourceImageRef(
                    slot_id="source",
                    read_url=backend.base + "/source.png",
                    content_type="image/png",
                    file_size=len(references[0][0]),
                    expires_at=datetime.now(UTC) + timedelta(hours=3),
                )
            ],
        )
        cfg = settings().model_copy(
            update={"project_service_base_url": backend.base, "internal_api_key": "benchmark-local-only"}
        )
        app = SimpleNamespace(
            worker_context=lambda _: (context, review, []),
            complete_run_delivery=lambda rid, status: setattr(backend, "delivered", status),
        )
        stack.enter_context(patch.object(tasks, "application", app))
        stack.enter_context(patch.object(media, "settings", lambda: cfg))
        stack.enter_context(patch.object(provider, "emit", provider_event))
        for name, attr in [
            ("plan", "plan"),
            ("copy", "_write_copy"),
            ("source_download", "_references"),
            ("images", "_generate_source_images"),
            ("render", "_render_blocks"),
            ("upload", "_upload_outputs"),
        ]:
            stack.enter_context(patch.object(tasks, attr, timed(name, getattr(tasks, attr))))
        for name in ("structured", "image"):
            stack.enter_context(patch.object(provider, name, timed(name, getattr(provider, name), call=True)))
        original_complete = media.BackendClient.complete
        stack.enter_context(
            patch.object(media.BackendClient, "complete", timed("callback", original_complete))
        )
        record({"event": "run_started", **result})
        started = time.perf_counter()
        try:
            tasks.generate({"id": run_id, "project_id": "benchmark-local"})
            result["status"] = backend.delivered
            body = backend.completion
            assert body is not None and backend.delivered == body.status
            result["successful_blocks"] = len(body.successful_images)
            result["failed_blocks"] = [slot.slot_id for slot in body.failed_slots]
            if body.status == "succeeded":
                assert len(backend.uploaded) == result["expected_blocks"]
        except Exception as exc:  # noqa: BLE001 - record every failed benchmark, including provider errors
            result.update(
                status="failed", error_type=type(exc).__name__, status_code=getattr(exc, "code", None)
            )
        except KeyboardInterrupt:
            result["status"] = "interrupted"
            raise
        finally:
            result.update(
                total_seconds=round(time.perf_counter() - started, 3),
                finished_at=datetime.now(UTC).isoformat(),
                stages=stages,
                image_attempts=sum(c["stage"] == "image" for c in calls),
                copy_attempts=sum(c["stage"] == "structured" for c in calls),
                provider_retries=len(retries),
                retry_wait_seconds=sum(r["delay_seconds"] for r in retries),
                uploaded_bytes=sum(u["file_size"] for u in backend.uploaded.values()),
            )
            record({"event": "run_finished", **result})
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--strengths", type=int, choices=[3, 12], default=3)
    parser.add_argument("--output", type=Path, required=True, help="New JSONL metadata file")
    args = parser.parse_args()
    if args.runs < 1:
        raise SystemExit("--runs must be at least 1")

    from funding_story.config import settings

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as output:

        def record(value):
            line = json.dumps(value, ensure_ascii=False)
            print(line, flush=True)
            output.write(line + "\n")
            output.flush()

        cfg = settings()
        paths = [
            "src/funding_story/tasks.py",
            "src/funding_story/provider.py",
            "src/funding_story/graph.py",
            "src/funding_story/renderer.py",
            "src/funding_story/resources/template.json",
            __file__,
        ]
        record(
            {
                "event": "environment",
                "git_head": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
                ).strip(),
                "python": platform.python_version(),
                "platform": platform.platform(),
                "model_profile": cfg.model_profile,
                "text_model": cfg.text_model,
                "image_provider": cfg.image_provider,
                "image_model": cfg.image_model,
                "location": "global" if cfg.image_provider == "openai" else cfg.google_cloud_location,
                "parallel_runs": 1,
                "backend": "loopback HTTP with PNG/DTO validation",
                "scope": "worker_start_to_callback_ack",
                "excluded": ["queue delay", "deployed BE/storage network latency"],
                "source_sha256": {
                    str(Path(p).relative_to(ROOT) if Path(p).is_absolute() else p): hashlib.sha256(
                        (ROOT / p).read_bytes()
                    ).hexdigest()
                    for p in paths
                },
            }
        )
        results = [run_once(i, args.strengths, record) for i in range(1, args.runs + 1)]
        totals = [r["total_seconds"] for r in results if r["status"] == "succeeded"]
        record(
            {
                "event": "summary",
                "attempted_count": len(results),
                "successful_count": len(totals),
                "incomplete_count": len(results) - len(totals),
                "mean_seconds": round(statistics.fmean(totals), 3) if totals else None,
                "p95_observed_seconds": percentile(totals, 0.95) if len(totals) >= 20 else None,
                "max_observed_seconds": max(totals) if totals else None,
            }
        )


if __name__ == "__main__":
    main()
