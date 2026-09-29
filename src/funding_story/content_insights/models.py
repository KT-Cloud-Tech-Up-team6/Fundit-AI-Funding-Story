from enum import Enum
from html.parser import HTMLParser
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


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
    WHAT = "WHAT"
    WHY = "WHY"


class ProjectReward(StrictModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    price: int | None = Field(default=None, ge=0)


class _EmbeddedMediaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.has_media = False

    def handle_starttag(self, tag, attrs):
        if tag in ("img", "picture", "video", "source", "iframe"):
            self.has_media = True

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)


class ProjectTextBlock(StrictModel):
    type: Literal["TEXT"]
    value: str = Field(min_length=1, max_length=20000)

    @field_validator("value")
    @classmethod
    def reject_embedded_media(cls, value):
        parser = _EmbeddedMediaParser()
        parser.feed(value)
        if parser.has_media:
            raise ValueError("TEXT HTML의 미디어는 별도 IMAGE 블록으로 전달해야 합니다.")
        return value


class ProjectImageBlock(StrictModel):
    type: Literal["IMAGE"]
    value: HttpUrl
    read_url: HttpUrl | None = None
    content_type: Literal["image/jpeg", "image/png", "image/webp"] | None = None
    file_size: int | None = Field(default=None, gt=0, le=10 * 1024 * 1024)
    expires_at: AwareDatetime | None = None

    @field_validator("value")
    @classmethod
    def require_https(cls, value):
        if value.scheme != "https":
            raise ValueError("이미지 참조는 HTTPS URL이어야 합니다.")
        if value.query or value.fragment:
            raise ValueError("이미지 참조에는 만료되는 서명 URL을 사용할 수 없습니다.")
        if not value.path.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
            raise ValueError("이번 Page Summary 입력은 JPEG·PNG·WebP 이미지만 지원합니다.")
        return value

    @field_validator("read_url")
    @classmethod
    def require_https_read_url(cls, value):
        if value is not None and value.scheme != "https":
            raise ValueError("이미지 읽기 URL은 HTTPS여야 합니다.")
        return value

    @model_validator(mode="after")
    def require_complete_read_reference(self):
        fields = (self.read_url, self.content_type, self.file_size, self.expires_at)
        if any(value is not None for value in fields) and not all(value is not None for value in fields):
            raise ValueError("이미지 읽기 URL·MIME·크기·만료 시각을 함께 전달해야 합니다.")
        return self


ProjectContentBlock = Annotated[ProjectTextBlock | ProjectImageBlock, Field(discriminator="type")]


class ProjectSnapshot(StrictModel):
    title: str = Field(min_length=1, max_length=200)
    category: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=20000)
    rewards: list[ProjectReward] = Field(default_factory=list, max_length=100)
    story_content: list[ProjectContentBlock] = Field(default_factory=list, max_length=300)

    @model_validator(mode="after")
    def require_grounded_content(self):
        has_story = any(
            block.type == "IMAGE" or (block.type == "TEXT" and block.value.strip())
            for block in self.story_content
        )
        has_reward = any(reward.description.strip() for reward in self.rewards)
        if not self.description.strip() and not has_story and not has_reward:
            raise ValueError("요약할 프로젝트 설명·본문·리워드 설명 중 하나가 필요합니다.")
        if sum(block.type == "IMAGE" for block in self.story_content) > 30:
            raise ValueError("프로젝트 본문 이미지는 최대 30개입니다.")
        return self


class ContentInsightCreateRequest(StrictModel):
    source_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=120)
    trigger: ContentInsightTrigger
    project_snapshot: ProjectSnapshot


class StorylineSection(StrictModel):
    role: StorylineSectionRole
    headline: str = Field(min_length=2, max_length=120)
    description: str = Field(min_length=2, max_length=400)

    @field_validator("headline", "description")
    @classmethod
    def validate_display_line(cls, value):
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("요약 블록의 헤드라인과 상세 설명은 비어 있을 수 없습니다.")
        if "\n" in normalized or "\r" in normalized:
            raise ValueError("요약 블록의 헤드라인과 상세 설명은 각각 한 줄이어야 합니다.")
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
            raise ValueError("요약 블록은 '~다' 또는 '~습니다' 종결형이 아닌 개조식이어야 합니다.")
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
    sections: list[StorylineSection] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def require_fixed_section_order(self):
        roles = [section.role for section in self.sections]
        expected = [
            StorylineSectionRole.WHAT,
            StorylineSectionRole.WHY,
        ]
        if roles != expected:
            raise ValueError("페이지 요약은 WHAT, WHY 순서여야 합니다.")
        return self


class StorylineDraft(StrictModel):
    content: str = Field(min_length=10, max_length=600)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value):
        return value.strip()
