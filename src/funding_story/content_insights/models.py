from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArtifactType(str, Enum):
    PAGE_SUMMARY = "PAGE_SUMMARY"
    STORYLINE = "STORYLINE"


class ContentInsightTrigger(str, Enum):
    PROJECT_REGISTRATION_COMPLETED = "PROJECT_REGISTRATION_COMPLETED"
    PROJECT_CONTENT_UPDATED = "PROJECT_CONTENT_UPDATED"
    STORY_CONFIRMED = "STORY_CONFIRMED"


class ArtifactStatus(str, Enum):
    NOT_REQUESTED = "NOT_REQUESTED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    STALE = "STALE"


class RunStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIALLY_SUCCEEDED = "PARTIALLY_SUCCEEDED"
    FAILED = "FAILED"
    STALE = "STALE"


class StorylineSectionRole(str, Enum):
    REWARD_IDENTITY = "REWARD_IDENTITY"
    PROJECT_REASON = "PROJECT_REASON"


class ProjectReward(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    price: int | None = Field(default=None, ge=0)


class ProjectContentBlock(StrictModel):
    type: Literal["TEXT"]
    value: str = Field(min_length=1, max_length=20000)


class ProjectSnapshot(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    category: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=20000)
    rewards: list[ProjectReward] = Field(default_factory=list, max_length=100)
    story_content: list[ProjectContentBlock] = Field(default_factory=list, max_length=300)

    @model_validator(mode="after")
    def require_grounded_content(self):
        has_story = any(block.type == "TEXT" and block.value.strip() for block in self.story_content)
        has_reward = any(reward.description.strip() for reward in self.rewards)
        if not self.description.strip() and not has_story and not has_reward:
            raise ValueError("요약할 프로젝트 설명·텍스트 본문·리워드 설명 중 하나가 필요합니다.")
        return self


class ContentInsightCreateRequest(StrictModel):
    source_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=120)
    trigger: ContentInsightTrigger
    requested_artifacts: list[ArtifactType] = Field(default_factory=list, max_length=2)
    project_snapshot: ProjectSnapshot

    @field_validator("requested_artifacts")
    @classmethod
    def unique_artifacts(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("requested_artifacts는 중복될 수 없습니다.")
        return value


class StorylineSection(StrictModel):
    role: StorylineSectionRole
    headline: str = Field(min_length=2, max_length=120)
    description: str = Field(min_length=2, max_length=400)

    @field_validator("headline", "description")
    @classmethod
    def validate_display_line(cls, value):
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("스토리라인의 헤드라인과 상세 설명은 비어 있을 수 없습니다.")
        if "\n" in normalized or "\r" in normalized:
            raise ValueError("스토리라인의 헤드라인과 상세 설명은 각각 한 줄이어야 합니다.")
        if any(label in normalized.upper() for label in ("WHAT", "WHY", "DIFFERENCE")):
            raise ValueError("내부 의미 구분명은 사용자 노출 문구에 포함할 수 없습니다.")
        stem = normalized.rstrip(" .!?。")
        if stem.endswith(
            (
                "습니다",
                "입니다",
                "합니다",
                "됩니다",
                "했습니다",
                "되었습니다",
                "이다",
                "하다",
                "한다",
                "된다",
                "준다",
                "낸다",
                "쓴다",
                "갖춘다",
                "줄인다",
                "높인다",
                "낮춘다",
                "돕는다",
                "만든다",
                "이어진다",
                "있다",
                "없다",
            )
        ):
            raise ValueError("스토리라인은 '~다' 또는 '~습니다' 종결형이 아닌 개조식이어야 합니다.")
        return normalized


class ArtifactOutput(StrictModel):
    schema_version: int = Field(default=1, ge=1)
    content: str | None = Field(default=None, min_length=1, max_length=4000)
    sections: list[StorylineSection] | None = Field(default=None, min_length=2, max_length=2)
    source_fields: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value):
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("생성 결과가 비어 있습니다.")
        return normalized

    @model_validator(mode="after")
    def require_one_output_shape(self):
        if (self.content is None) == (self.sections is None):
            raise ValueError("content 또는 sections 중 정확히 하나가 필요합니다.")
        return self


class ArtifactError(StrictModel):
    code: str
    retryable: bool
    message: str | None = None


class ArtifactView(StrictModel):
    artifact_id: str | None = None
    status: ArtifactStatus
    required: bool
    attempts: int = 0
    output: ArtifactOutput | None = None
    prompt_version: str | None = None
    model: str | None = None
    error: ArtifactError | None = None


class ContentInsightRunResponse(StrictModel):
    run_id: str
    revision: int = Field(ge=1)
    status: RunStatus
    source_revision: int = Field(ge=1)
    required_artifacts_ready: bool
    artifacts: dict[ArtifactType, ArtifactView]


class PageSummaryDraft(StrictModel):
    content: str = Field(min_length=10, max_length=600)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value):
        return value.strip()


class StorylineDraft(StrictModel):
    sections: list[StorylineSection] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def require_fixed_section_order(self):
        roles = [section.role for section in self.sections]
        expected = [
            StorylineSectionRole.REWARD_IDENTITY,
            StorylineSectionRole.PROJECT_REASON,
        ]
        if roles != expected:
            raise ValueError("스토리라인은 리워드 정체성, 프로젝트 필요성 순서여야 합니다.")
        return self
