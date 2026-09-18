from time import perf_counter

from ..config import settings
from ..observability import emit
from .generators import GENERATOR_REGISTRY, ArtifactGenerator
from .models import ArtifactType, ProjectSnapshot
from .service import ContentInsightsApplication


def execute_artifact(
    application: ContentInsightsApplication,
    artifact_id: str,
    generators: dict[ArtifactType, ArtifactGenerator] | None = None,
) -> None:
    registry = generators or GENERATOR_REGISTRY
    with application.claim_artifact(artifact_id) as artifact:
        if artifact is None:
            return
        snapshot_data, artifact_type = application.artifact_context(artifact)
        generator = registry[artifact_type]
        started = perf_counter()
        try:
            output = generator.generate(artifact_id, ProjectSnapshot.model_validate(snapshot_data))
            application.complete_artifact(
                artifact_id,
                output,
                prompt_version=generator.prompt_version,
                model=settings().text_model,
            )
            emit(
                "content_insight_artifact_succeeded",
                artifact_id=artifact_id,
                artifact_type=artifact_type.value,
                run_id=artifact["data"]["run_id"],
                project_id=artifact["project_id"],
                source_revision=artifact["data"]["source_revision"],
                attempt=artifact["data"]["attempts"],
                prompt_version=generator.prompt_version,
                duration_ms=round((perf_counter() - started) * 1000),
            )
        except Exception as exc:  # noqa: BLE001 - terminal artifact state must be durable
            application.fail_artifact(
                artifact_id,
                exc,
                prompt_version=generator.prompt_version,
                model=settings().text_model,
            )
            emit(
                "content_insight_artifact_failed",
                artifact_id=artifact_id,
                artifact_type=artifact_type.value,
                run_id=artifact["data"]["run_id"],
                project_id=artifact["project_id"],
                source_revision=artifact["data"]["source_revision"],
                attempt=artifact["data"]["attempts"],
                prompt_version=generator.prompt_version,
                duration_ms=round((perf_counter() - started) * 1000),
                error_type=type(exc).__name__,
            )
