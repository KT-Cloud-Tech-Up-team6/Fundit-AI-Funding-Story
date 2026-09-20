from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, model_validator

PublicId = Annotated[
    str,
    Field(pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ErrorResponse(StrictModel):
    code: str
    message: str
    detail: Any | None = None


class AsyncError(StrictModel):
    code: str
    message: str
    retryable: bool
    detail: Any | None = None


class CategoryFact(StrictModel):
    major: str = Field(min_length=1, max_length=200)
    minor: str = Field(min_length=1, max_length=200)


class RewardOption(StrictModel):
    group_name: str = Field(min_length=1, max_length=100)
    values: list[str] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_nonempty_values(self):
        stripped = [value.strip() for value in self.values]
        if any(not value for value in stripped) or len(set(stripped)) != len(stripped):
            raise ValueError("리워드 옵션 값은 비어 있거나 중복될 수 없습니다.")
        self.values = stripped
        return self


class RewardFact(StrictModel):
    reward_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=3000)
    price: int = Field(ge=0)
    is_limited: bool
    quantity: int | None
    is_early_bird: bool
    options: list[RewardOption] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def quantity_matches_limit(self):
        if self.is_limited and (self.quantity is None or self.quantity < 0):
            raise ValueError("수량 제한 리워드는 0 이상의 quantity가 필요합니다.")
        if not self.is_limited and self.quantity is not None:
            raise ValueError("무제한 리워드의 quantity는 null이어야 합니다.")
        return self


class ProjectFact(StrictModel):
    business_type: Literal["GENERAL", "SOLE", "CORP"]
    category: CategoryFact
    title: str = Field(min_length=1, max_length=40)
    goal_amount: int = Field(ge=500_000)


class SourceImageRef(StrictModel):
    slot_id: str = Field(min_length=1, max_length=200)
    reward_id: int | None = Field(default=None, gt=0)
    read_url: HttpUrl
    content_type: Literal["image/jpeg", "image/png", "image/webp"]
    file_size: int = Field(gt=0, le=10 * 1024 * 1024)
    expires_at: AwareDatetime


class FundingStoryContext(StrictModel):
    project: ProjectFact
    rewards: list[RewardFact] = Field(min_length=1, max_length=3)
    source_images: list[SourceImageRef] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def unique_ids(self):
        reward_ids = [reward.reward_id for reward in self.rewards]
        slots = [image.slot_id for image in self.source_images]
        if len(set(reward_ids)) != len(reward_ids):
            raise ValueError("reward_id는 중복될 수 없습니다.")
        if len(set(slots)) != len(slots):
            raise ValueError("source image slot_id는 중복될 수 없습니다.")
        allowed = set(reward_ids)
        if any(image.reward_id is not None and image.reward_id not in allowed for image in self.source_images):
            raise ValueError("source image reward_id는 현재 프로젝트 리워드여야 합니다.")
        return self


class SessionCreateRequest(StrictModel):
    context: FundingStoryContext


class Strength(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=3000)


class Problem(StrictModel):
    heading: str = Field(min_length=1, max_length=80)
    body: str = Field(min_length=1, max_length=600)


class Review(StrictModel):
    reply: str = Field(min_length=1)
    product: str = ""
    story: str = ""
    strengths: list[Strength] = Field(default_factory=list, max_length=12)
    problems: list[Problem] = Field(default_factory=list, max_length=4)
    missing: list[str] = Field(default_factory=list)
    tone: str | None = Field(default=None, max_length=500)
    brand_color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")

    @model_validator(mode="after")
    def unique_strengths(self):
        if len({strength.id for strength in self.strengths}) != len(self.strengths):
            raise ValueError("강점 ID 중복")
        return self


class MessageRequest(StrictModel):
    message_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=12000)


class ConfirmRequest(StrictModel):
    revision: int = Field(ge=1)


class RunRequest(StrictModel):
    session_id: PublicId
    confirmed_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=120)
    context: FundingStoryContext


class ChatMessage(StrictModel):
    role: Literal["assistant", "user"]
    text: str


class StrengthSummary(StrictModel):
    title: str
    description: str


class StorySummary(StrictModel):
    product: str
    story: str
    strengths: list[StrengthSummary]


class SessionResponse(StrictModel):
    session_id: PublicId
    revision: int
    confirmed_revision: int | None
    messages: list[ChatMessage]
    missing: list[str]
    summary: StorySummary | None
    active_chat_id: PublicId | None


class LatestSessionResponse(StrictModel):
    session: SessionResponse | None


class ChatAcceptedResponse(StrictModel):
    chat_id: PublicId
    status: Literal["queued"]


class ChatDoneEvent(StrictModel):
    chat_id: PublicId
    status: Literal["succeeded", "failed"]
    session_id: PublicId
    revision: int
    error: AsyncError | None = None


class ConfirmResponse(StrictModel):
    session_id: PublicId
    confirmed_revision: int


class RunAcceptedResponse(StrictModel):
    run_id: PublicId
    status: Literal["queued"]


class OutputDescriptor(StrictModel):
    slot_id: str = Field(min_length=1, max_length=200)
    file_name: str = Field(pattern=r"^.+\.png$", max_length=255)
    content_type: Literal["image/png"]
    file_size: int = Field(gt=0, le=10 * 1024 * 1024)


class UploadTargetsRequest(StrictModel):
    outputs: list[OutputDescriptor] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def unique_slots(self):
        slots = [output.slot_id for output in self.outputs]
        if len(set(slots)) != len(slots):
            raise ValueError("출력 slot_id는 중복될 수 없습니다.")
        return self


class UploadTarget(StrictModel):
    slot_id: str
    upload_url: HttpUrl
    file_url: HttpUrl
    expires_at: AwareDatetime


class UploadTargetsResponse(StrictModel):
    targets: list[UploadTarget]


class TextContentBlock(StrictModel):
    type: Literal["TEXT"]
    value: str = Field(min_length=1)


class ImageContentBlock(StrictModel):
    type: Literal["IMAGE"]
    slot_id: str = Field(min_length=1, max_length=200)


class GeneratedBody(StrictModel):
    cover_image_slot_id: str | None
    intro_content: list[TextContentBlock | ImageContentBlock]


class SuccessfulImage(StrictModel):
    slot_id: str = Field(min_length=1, max_length=200)
    file_url: HttpUrl
    content_type: Literal["image/png"]
    file_size: int = Field(gt=0, le=10 * 1024 * 1024)
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class FailedSlot(StrictModel):
    slot_id: str = Field(min_length=1, max_length=200)
    stage: Literal["generation", "rendering", "upload"]
    error: AsyncError


class RunCompletionRequest(StrictModel):
    status: Literal["succeeded", "partially_succeeded", "failed"]
    generated_body: GeneratedBody | None
    successful_images: list[SuccessfulImage]
    failed_slots: list[FailedSlot]
    error: AsyncError | None

    @model_validator(mode="after")
    def status_invariants(self):
        if self.status == "succeeded" and (
            self.generated_body is None or not self.successful_images or self.failed_slots or self.error
        ):
            raise ValueError("succeeded 완료 결과가 올바르지 않습니다.")
        if self.status == "partially_succeeded" and (
            self.generated_body is None
            or not self.successful_images
            or not self.failed_slots
            or self.error is not None
        ):
            raise ValueError("partially_succeeded 완료 결과가 올바르지 않습니다.")
        if self.status == "failed" and (
            self.generated_body is not None or self.successful_images or self.error is None
        ):
            raise ValueError("failed 완료 결과가 올바르지 않습니다.")
        successful_slots = [image.slot_id for image in self.successful_images]
        if len(set(successful_slots)) != len(successful_slots):
            raise ValueError("성공 이미지 slot_id는 중복될 수 없습니다.")
        failed_slots = [failure.slot_id for failure in self.failed_slots]
        if len(set(failed_slots)) != len(failed_slots):
            raise ValueError("실패 slot_id는 중복될 수 없습니다.")
        if set(successful_slots) & set(failed_slots):
            raise ValueError("같은 slot_id를 성공과 실패로 동시에 전달할 수 없습니다.")
        if self.generated_body is not None:
            referenced = {
                block.slot_id
                for block in self.generated_body.intro_content
                if isinstance(block, ImageContentBlock)
            }
            if self.generated_body.cover_image_slot_id is not None:
                referenced.add(self.generated_body.cover_image_slot_id)
            if not referenced.issubset(set(successful_slots)):
                raise ValueError("본문 이미지 참조는 성공 이미지 slot_id여야 합니다.")
        return self


class RunCompletionResponse(StrictModel):
    run_id: PublicId
    status: Literal["succeeded", "partially_succeeded", "failed"]


class ProjectInput(StrictModel):
    """Internal generation model; never accepted directly at the HTTP boundary."""

    business_type: Literal["GENERAL", "SOLE", "CORP"]
    category: str
    goal_amount: int = Field(ge=500_000)
    title: str = Field(min_length=1, max_length=40)
    rewards: list[RewardFact] = Field(min_length=1, max_length=3)
    source_images: list[SourceImageRef] = Field(default_factory=list, max_length=30)
    tone: str = Field(default="", max_length=500)
    brand_color: str = Field(default="#647895", pattern=r"^#[0-9a-fA-F]{6}$")

    @classmethod
    def from_context(cls, context: FundingStoryContext, review: Review | None = None):
        return cls(
            business_type=context.project.business_type,
            category=f"{context.project.category.major} / {context.project.category.minor}",
            goal_amount=context.project.goal_amount,
            title=context.project.title,
            rewards=context.rewards,
            source_images=context.source_images,
            tone=(review.tone or "") if review else "",
            brand_color=(review.brand_color or "#647895") if review else "#647895",
        )


class CopyResult(StrictModel):
    texts: dict[str, str]
    image_prompts: dict[str, str]
    summary: str
    storyline: str
