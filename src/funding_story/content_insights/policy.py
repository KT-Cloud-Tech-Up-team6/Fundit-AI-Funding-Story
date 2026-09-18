from dataclasses import dataclass

from .models import ArtifactType, ContentInsightTrigger

POLICY_VERSION = "content-insights-policy-v2"


@dataclass(frozen=True)
class ArtifactPolicy:
    requested: bool
    required: bool


def resolve_policy(
    trigger: ContentInsightTrigger, requested_artifacts: list[ArtifactType]
) -> dict[ArtifactType, ArtifactPolicy]:
    requested = set(requested_artifacts)
    if not requested:
        requested = (
            {ArtifactType.STORYLINE}
            if trigger == ContentInsightTrigger.STORY_CONFIRMED
            else {ArtifactType.PAGE_SUMMARY, ArtifactType.STORYLINE}
        )

    if trigger in (
        ContentInsightTrigger.PROJECT_REGISTRATION_COMPLETED,
        ContentInsightTrigger.PROJECT_CONTENT_UPDATED,
    ):
        return {
            ArtifactType.PAGE_SUMMARY: ArtifactPolicy(requested=True, required=True),
            ArtifactType.STORYLINE: ArtifactPolicy(requested=True, required=True),
        }

    if trigger == ContentInsightTrigger.STORY_CONFIRMED:
        unsupported = requested - {ArtifactType.STORYLINE}
        if unsupported:
            raise ValueError("STORY_CONFIRMED에서는 STORYLINE만 요청할 수 있습니다.")
        return {
            ArtifactType.PAGE_SUMMARY: ArtifactPolicy(requested=False, required=False),
            ArtifactType.STORYLINE: ArtifactPolicy(requested=True, required=True),
        }

    raise ValueError("지원하지 않는 Content Insights trigger입니다.")
