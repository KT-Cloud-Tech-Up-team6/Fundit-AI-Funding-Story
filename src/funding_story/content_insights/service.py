import hashlib
import json
from collections.abc import Iterator
from contextlib import contextmanager

from ..application import ApplicationConflict, ApplicationInvalid
from ..domain.repositories import Record, RecordRepository
from ..job_lease import leased_record
from .models import (
    ArtifactOutput,
    ArtifactStatus,
    ArtifactType,
    ContentInsightCreateRequest,
    RunStatus,
)
from .policy import POLICY_VERSION, resolve_policy

RUN_KIND = "content_insight_run"
ARTIFACT_KIND = "content_insight_artifact"


def _fingerprint(body: ContentInsightCreateRequest) -> str:
    canonical = json.dumps(
        body.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _source_hash(body: ContentInsightCreateRequest) -> str:
    canonical = json.dumps(
        body.project_snapshot.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _aggregate_status(statuses: list[ArtifactStatus]) -> RunStatus:
    requested = [status for status in statuses if status != ArtifactStatus.NOT_REQUESTED]
    if not requested:
        return RunStatus.FAILED
    if any(status == ArtifactStatus.STALE for status in requested):
        return RunStatus.STALE
    if all(status == ArtifactStatus.SUCCEEDED for status in requested):
        return RunStatus.SUCCEEDED
    if all(status == ArtifactStatus.FAILED for status in requested):
        return RunStatus.FAILED
    if any(status == ArtifactStatus.RUNNING for status in requested):
        return RunStatus.RUNNING
    if any(status == ArtifactStatus.QUEUED for status in requested):
        return (
            RunStatus.QUEUED
            if all(status == ArtifactStatus.QUEUED for status in requested)
            else RunStatus.RUNNING
        )
    return RunStatus.PARTIALLY_SUCCEEDED


class ContentInsightsApplication:
    """Application service for one facade run with independently durable artifacts."""

    def __init__(self, records: RecordRepository):
        self._records = records

    def create_run(
        self,
        body: ContentInsightCreateRequest,
        project: str,
    ) -> tuple[dict, list[tuple[str, ArtifactType]]]:
        try:
            policy = resolve_policy(body.trigger, body.requested_artifacts)
        except ValueError as exc:
            raise ApplicationInvalid(str(exc)) from exc

        fingerprint = _fingerprint(body)
        source_hash = _source_hash(body)
        request_key = "content-insights:" + body.idempotency_key
        with self._records.transaction() as tx:
            tx.lock_request(project + ":" + request_key)
            tx.lock_request(project + ":content-insights-revision")
            existing = tx.get_request(project, request_key)
            if existing:
                if existing["fingerprint"] != fingerprint:
                    raise ApplicationConflict("중복 키의 입력이 다릅니다.")
                return self._public_run(tx, tx.get(existing["record_id"], project, kind=RUN_KIND)), []

            previous = tx.latest(project, RUN_KIND)
            if previous:
                previous_revision = int(previous["data"]["source_revision"])
                if previous_revision > body.source_revision:
                    raise ApplicationConflict("더 최신 프로젝트 입력 버전이 이미 처리되었습니다.")
                if previous_revision == body.source_revision:
                    raise ApplicationConflict("같은 프로젝트 입력 버전의 생성 작업이 이미 존재합니다.")
                self._mark_stale(tx, previous)

            parent_data = {
                "status": RunStatus.QUEUED.value,
                "source_revision": body.source_revision,
                "source_hash": source_hash,
                "trigger": body.trigger.value,
                "policy_version": POLICY_VERSION,
                "generation_policy": {
                    artifact_type.value: {
                        "requested": artifact_policy.requested,
                        "required": artifact_policy.required,
                    }
                    for artifact_type, artifact_policy in policy.items()
                },
                "project_snapshot": body.project_snapshot.model_dump(mode="json"),
                "artifacts": {},
                "required_artifacts_ready": False,
            }
            run_id = tx.create(project, RUN_KIND, parent_data)
            dispatch = []
            for artifact_type in ArtifactType:
                artifact_policy = policy[artifact_type]
                if not artifact_policy.requested:
                    parent_data["artifacts"][artifact_type.value] = None
                    continue
                artifact_id = tx.create(
                    project,
                    ARTIFACT_KIND,
                    {
                        "run_id": run_id,
                        "artifact_type": artifact_type.value,
                        "source_revision": body.source_revision,
                        "source_hash": source_hash,
                        "job_key": f"{request_key}:{artifact_type.value}",
                        "required": artifact_policy.required,
                        "status": ArtifactStatus.QUEUED.value,
                        "attempts": 0,
                        "schema_version": 2 if artifact_type == ArtifactType.STORYLINE else 1,
                        "output": None,
                        "error": None,
                        "prompt_version": None,
                        "model": None,
                    },
                )
                parent_data["artifacts"][artifact_type.value] = artifact_id
                dispatch.append((artifact_id, artifact_type))
            tx.save(run_id, parent_data)
            tx.add_request(project, request_key, fingerprint, run_id)
            result = self._public_run(tx, tx.get(run_id, project, kind=RUN_KIND))
        return result, dispatch

    def get_run(self, run_id: str, project: str) -> dict:
        return self._public_run(self._records, self._records.get(run_id, project, kind=RUN_KIND))

    def retry_artifact(
        self,
        run_id: str,
        artifact_type: ArtifactType,
        project: str,
    ) -> tuple[dict, tuple[str, ArtifactType]]:
        with self._records.transaction() as tx:
            tx.lock_request(project + ":content-insights-artifact:" + run_id + ":" + artifact_type.value)
            parent = tx.get(run_id, project, lock=True, kind=RUN_KIND)
            if parent["data"]["status"] == RunStatus.STALE.value:
                raise ApplicationConflict("최신 프로젝트 입력 버전의 작업만 재시도할 수 있습니다.")
            artifact_id = parent["data"]["artifacts"].get(artifact_type.value)
            if not artifact_id:
                raise ApplicationConflict("요청되지 않은 결과는 재시도할 수 없습니다.")
            artifact = tx.get(artifact_id, project, lock=True, kind=ARTIFACT_KIND)
            if artifact["data"]["status"] != ArtifactStatus.FAILED.value:
                raise ApplicationConflict("실패한 결과만 재시도할 수 있습니다.")
            if not artifact["data"].get("error", {}).get("retryable", False):
                raise ApplicationConflict("재시도할 수 없는 실패입니다. 새 프로젝트 revision으로 요청하세요.")
            artifact["data"].update(
                status=ArtifactStatus.QUEUED.value,
                error=None,
                output=None,
            )
            tx.save(artifact_id, artifact["data"])
            self._refresh_parent(tx, run_id, project)
            result = self._public_run(tx, tx.get(run_id, project, kind=RUN_KIND))
        return result, (artifact_id, artifact_type)

    def pending_job_ids(self) -> list[str]:
        return self._records.pending_job_ids()

    def pending_artifacts(self) -> list[tuple[str, ArtifactType]]:
        pending = []
        for record_id in self._records.pending_job_ids():
            try:
                row = self._records.get(record_id, kind=ARTIFACT_KIND)
            except LookupError:
                continue
            pending.append((record_id, ArtifactType(row["data"]["artifact_type"])))
        return pending

    def readiness(self) -> tuple[bool, str]:
        return self._records.ready()

    @contextmanager
    def claim_artifact(self, artifact_id: str) -> Iterator[Record | None]:
        with leased_record(
            self._records,
            artifact_id,
            queued=ArtifactStatus.QUEUED.value,
            running=ArtifactStatus.RUNNING.value,
        ) as artifact:
            if artifact is None:
                yield None
                return
            parent = self._records.get(
                artifact["data"]["run_id"], artifact["project_id"], kind=RUN_KIND
            )
            if parent["data"]["status"] == RunStatus.STALE.value:
                artifact["data"]["status"] = ArtifactStatus.STALE.value
                self._records.save(artifact_id, artifact["data"])
                yield None
                return
            artifact["data"].update(
                attempts=int(artifact["data"].get("attempts", 0)) + 1,
                error=None,
            )
            self._records.save(artifact_id, artifact["data"])
            self._refresh_parent(self._records, parent["id"], artifact["project_id"])
            yield artifact

    def artifact_context(self, artifact: Record) -> tuple[dict, ArtifactType]:
        parent = self._records.get(artifact["data"]["run_id"], artifact["project_id"], kind=RUN_KIND)
        return parent["data"]["project_snapshot"], ArtifactType(artifact["data"]["artifact_type"])

    def complete_artifact(
        self,
        artifact_id: str,
        output: ArtifactOutput,
        *,
        prompt_version: str,
        model: str,
    ) -> None:
        artifact = self._records.get(artifact_id, kind=ARTIFACT_KIND)
        if artifact["data"]["status"] != ArtifactStatus.RUNNING.value:
            raise ApplicationConflict("실행 중인 결과만 완료할 수 있습니다.")
        artifact_type = ArtifactType(artifact["data"]["artifact_type"])
        if artifact_type == ArtifactType.PAGE_SUMMARY and output.content is None:
            raise ApplicationInvalid("페이지 요약은 content 출력이 필요합니다.")
        if artifact_type == ArtifactType.STORYLINE and (
            output.sections is None or output.schema_version != 2
        ):
            raise ApplicationInvalid("스토리라인은 schema v2의 2개 section 출력이 필요합니다.")
        artifact["data"].update(
            status=ArtifactStatus.SUCCEEDED.value,
            output=output.model_dump(mode="json"),
            error=None,
            prompt_version=prompt_version,
            model=model,
        )
        self._records.save(artifact_id, artifact["data"])
        self._refresh_parent(
            self._records,
            artifact["data"]["run_id"],
            artifact["project_id"],
        )

    def fail_artifact(
        self,
        artifact_id: str,
        exc: Exception,
        *,
        prompt_version: str | None = None,
        model: str | None = None,
    ) -> None:
        artifact = self._records.get(artifact_id, kind=ARTIFACT_KIND)
        code = getattr(exc, "code", None)
        retryable = code in (429, 500, 502, 503, 504) or isinstance(exc, (TimeoutError, ConnectionError))
        artifact["data"].update(
            status=ArtifactStatus.FAILED.value,
            output=None,
            prompt_version=prompt_version,
            model=model,
            error={
                "code": str(code) if code is not None else type(exc).__name__.upper(),
                "retryable": retryable,
                "message": "생성 작업에 실패했습니다.",
            },
        )
        self._records.save(artifact_id, artifact["data"])
        self._refresh_parent(
            self._records,
            artifact["data"]["run_id"],
            artifact["project_id"],
        )

    @staticmethod
    def _mark_stale(repository: RecordRepository, parent: Record) -> None:
        for artifact_id in parent["data"].get("artifacts", {}).values():
            if not artifact_id:
                continue
            artifact = repository.get(
                artifact_id,
                parent["project_id"],
                kind=ARTIFACT_KIND,
            )
            artifact["data"]["status"] = ArtifactStatus.STALE.value
            repository.save(artifact_id, artifact["data"])
        parent["data"].update(
            status=RunStatus.STALE.value,
            required_artifacts_ready=False,
        )
        repository.save(parent["id"], parent["data"])

    @staticmethod
    def _refresh_parent(repository: RecordRepository, run_id: str, project: str) -> None:
        with repository.transaction() as tx:
            parent = tx.get(run_id, project, lock=True, kind=RUN_KIND)
            if parent["data"]["status"] == RunStatus.STALE.value:
                return
            artifacts = []
            required_ready = True
            for artifact_id in parent["data"]["artifacts"].values():
                if not artifact_id:
                    continue
                artifact = tx.get(artifact_id, project, kind=ARTIFACT_KIND)["data"]
                status = ArtifactStatus(artifact["status"])
                artifacts.append(status)
                if artifact["required"] and status != ArtifactStatus.SUCCEEDED:
                    required_ready = False
            parent["data"].update(
                status=_aggregate_status(artifacts).value,
                required_artifacts_ready=required_ready,
            )
            tx.save(run_id, parent["data"])

    @staticmethod
    def _public_run(repository: RecordRepository, parent: Record) -> dict:
        artifacts = {}
        for artifact_type in ArtifactType:
            artifact_id = parent["data"].get("artifacts", {}).get(artifact_type.value)
            if not artifact_id:
                artifacts[artifact_type.value] = {
                    "artifact_id": None,
                    "status": ArtifactStatus.NOT_REQUESTED.value,
                    "required": False,
                    "attempts": 0,
                    "output": None,
                    "prompt_version": None,
                    "model": None,
                    "error": None,
                }
                continue
            artifact = repository.get(
                artifact_id,
                parent["project_id"],
                kind=ARTIFACT_KIND,
            )["data"]
            artifacts[artifact_type.value] = {
                "artifact_id": artifact_id,
                "status": artifact["status"],
                "required": artifact["required"],
                "attempts": artifact.get("attempts", 0),
                "output": artifact.get("output"),
                "prompt_version": artifact.get("prompt_version"),
                "model": artifact.get("model"),
                "error": artifact.get("error"),
            }
        return {
            "run_id": parent["id"],
            "revision": parent["revision"],
            "status": parent["data"]["status"],
            "source_revision": parent["data"]["source_revision"],
            "required_artifacts_ready": parent["data"].get("required_artifacts_ready", False),
            "artifacts": artifacts,
        }
