import argparse
import signal
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Literal

from .bootstrap import application, close_pools, content_insights_application, open_pools
from .content_insights.models import ArtifactType
from .observability import emit
from .tasks import execute, execute_content_insight

Lane = Literal["all", "funding-story", "page-summary", "storyline"]


@dataclass(frozen=True)
class PendingJob:
    record_id: str
    lane: str
    artifact_type: ArtifactType | None = None

    @property
    def key(self):
        return self.lane, self.record_id


def pending_jobs(lane: Lane) -> list[PendingJob]:
    jobs = []
    if lane in ("all", "funding-story"):
        jobs.extend(PendingJob(record_id, "funding-story") for record_id in application.pending_job_ids())
    if lane in ("all", "page-summary", "storyline"):
        for record_id, artifact_type in content_insights_application.pending_artifacts():
            artifact_lane = (
                "page-summary"
                if artifact_type == ArtifactType.PAGE_SUMMARY
                else "storyline"
            )
            if lane in ("all", artifact_lane):
                jobs.append(PendingJob(record_id, artifact_lane, artifact_type))
    return jobs


def run_job(job: PendingJob) -> None:
    if job.artifact_type is None:
        execute(job.record_id)
    else:
        execute_content_insight(job.record_id, job.artifact_type)


def run_worker(lane: Lane, concurrency: int, poll_interval: float, stop: threading.Event) -> None:
    open_pools()
    futures: dict[Future, PendingJob] = {}
    active: set[tuple[str, str]] = set()
    last_cleanup = 0.0
    try:
        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix=f"ai-{lane}") as pool:
            while not stop.is_set():
                for future in [future for future in futures if future.done()]:
                    job = futures.pop(future)
                    active.discard(job.key)
                    try:
                        future.result()
                    except Exception as exc:  # noqa: BLE001 - lease expiry enables another attempt
                        emit(
                            "polling_job_failed",
                            record_id=job.record_id,
                            lane=job.lane,
                            error_type=type(exc).__name__,
                        )

                available = concurrency - len(futures)
                if available > 0:
                    for job in pending_jobs(lane):
                        if available <= 0:
                            break
                        if job.key in active:
                            continue
                        active.add(job.key)
                        futures[pool.submit(run_job, job)] = job
                        available -= 1

                now = time.monotonic()
                if lane in ("all", "funding-story") and now - last_cleanup >= 60:
                    removed = application.cleanup_expired()
                    if removed:
                        emit("funding_story_state_expired", count=removed)
                    last_cleanup = now
                stop.wait(poll_interval)
    finally:
        close_pools()


def main() -> None:
    parser = argparse.ArgumentParser(description="PostgreSQL polling worker")
    parser.add_argument(
        "--lane",
        choices=("all", "funding-story", "page-summary", "storyline"),
        default="all",
    )
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--poll-interval", type=float, default=0.5)
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")
    if args.poll_interval <= 0:
        parser.error("--poll-interval must be greater than 0")

    stop = threading.Event()

    def request_stop(*_):
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    run_worker(args.lane, args.concurrency, args.poll_interval, stop)


if __name__ == "__main__":
    main()
