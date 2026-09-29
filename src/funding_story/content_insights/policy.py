from dataclasses import dataclass

from .models import ArtifactType, ContentInsightTrigger

POLICY_VERSION = "content-insights-policy-v3"


@dataclass(frozen=True)
class ArtifactPolicy:
    requested: bool
    required: bool


def resolve_policy(trigger: ContentInsightTrigger, artifact_type: ArtifactType) -> dict[ArtifactType, ArtifactPolicy]:
    if artifact_type == ArtifactType.PAGE_SUMMARY and trigger in (
        ContentInsightTrigger.PROJECT_REGISTRATION_COMPLETED,
        ContentInsightTrigger.PROJECT_CONTENT_UPDATED,
    ):
        return {
            ArtifactType.PAGE_SUMMARY: ArtifactPolicy(requested=True, required=True),
            ArtifactType.STORYLINE: ArtifactPolicy(requested=False, required=False),
        }

    if artifact_type == ArtifactType.STORYLINE and trigger == ContentInsightTrigger.STORY_CONFIRMED:
        return {
            ArtifactType.PAGE_SUMMARY: ArtifactPolicy(requested=False, required=False),
            ArtifactType.STORYLINE: ArtifactPolicy(requested=True, required=True),
        }

    raise ValueError("요청한 결과와 trigger 조합이 맞지 않습니다.")
