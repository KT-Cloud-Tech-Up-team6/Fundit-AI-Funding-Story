from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Reward(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=3000)
    price: int = Field(ge=0)
    normal_price: int | None = Field(default=None, ge=0)
    product_count: int = Field(default=1, ge=1, le=20)


class Strength(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=3000)


class Problem(StrictModel):
    heading: str = Field(min_length=1, max_length=80)
    body: str = Field(min_length=1, max_length=600)


class ProjectInput(StrictModel):
    category: str = Field(default="", max_length=200)
    goal_amount: int | None = Field(default=None, ge=0)
    title: str = Field(min_length=1, max_length=200)
    product_description: str = Field(default="", max_length=20000)
    rewards: list[Reward] = Field(default_factory=list, max_length=3)
    asset_ids: list[str] = Field(default_factory=list, max_length=30)
    tone: str = Field(default="", max_length=500)
    brand_color: str = Field(default="#647895", pattern=r"^#[0-9a-fA-F]{6}$")
    information: dict[str, str] = Field(default_factory=dict)


class InputChange(StrictModel):
    field: Literal[
        "tone",
        "price",
        "normal_price",
        "product_count",
        "budget",
        "schedule",
        "team",
        "policy",
        "risks",
    ]
    reward_index: int | None = None
    value: str = Field(min_length=1, max_length=12000)
    quote: str = Field(min_length=1, max_length=12000)


class Review(StrictModel):
    include_information: bool = False
    information_reason: str = ""
    input_changes: list[InputChange] = Field(default_factory=list, max_length=20)
    reply: str = Field(min_length=1)
    strengths: list[Strength] = Field(default_factory=list, max_length=12)
    problems: list[Problem] = Field(default_factory=list, max_length=4)
    missing: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_strengths(self):
        if len({s.id for s in self.strengths}) != len(self.strengths):
            raise ValueError("강점 ID 중복")
        return self


class MessageRequest(StrictModel):
    message_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=12000)
    asset_ids: list[str] = Field(default_factory=list, max_length=30)


class ConfirmRequest(StrictModel):
    revision: int = Field(ge=1)


class RunRequest(StrictModel):
    session_id: str
    revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=120)


class ExportRequest(StrictModel):
    source_input_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=120)
    text_overrides: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_overrides(self):
        if len(self.text_overrides) > 300:
            raise ValueError("문구 수정은 최대 300개입니다.")
        if any(not key or len(key) > 200 for key in self.text_overrides):
            raise ValueError("문구 슬롯 ID가 올바르지 않습니다.")
        if any(len(value) > 10000 for value in self.text_overrides.values()):
            raise ValueError("문구 길이가 너무 깁니다.")
        return self


class ExportCommitRequest(StrictModel):
    document_revision: int = Field(ge=1)


class ExportImage(StrictModel):
    block_id: str
    order: int = Field(ge=0)
    asset_id: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    alt: str


class ProjectSummary(StrictModel):
    summary: str
    storyline: str


class ExportResult(StrictModel):
    id: str
    revision: int = Field(ge=1)
    status: Literal["rendering", "succeeded", "failed"]
    run_id: str
    source_input_revision: int = Field(ge=1)
    schema_version: int | None = None
    images: list[ExportImage] = Field(default_factory=list)
    information: dict[str, str] = Field(default_factory=dict)
    fixed_content: dict[str, str] = Field(default_factory=dict)
    project_summary: ProjectSummary | None = None
    committed: bool = False
    document_revision: int | None = None
    committed_at: str | None = None
    cleanup_pending: bool | None = None
    error: str | None = None


class CopyResult(StrictModel):
    texts: dict[str, str]
    image_prompts: dict[str, str]
    summary: str
    storyline: str


def output_information(information: dict[str, str]) -> dict[str, str]:
    """Gift details belong to platform registration, not generated story sections."""
    return {key: value for key, value in information.items() if key != "gift_details"}
