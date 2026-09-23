import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from funding_story.graph import format_graph
from funding_story.models import (
    AsyncError,
    CopyResult,
    FailedSlot,
    GeneratedBody,
    ImageContentBlock,
    ProjectInput,
    Review,
    RunCompletionRequest,
    SuccessfulImage,
)
from funding_story.planner import CATALOG, CATEGORIES, TEMPLATE, plan, requirements, validate_copy
from funding_story.provider import transient_call


def review():
    return Review(
        reply="확인",
        product="작은 공간을 위한 무선 청소기",
        story="자주 청소하는 사용자의 부담을 줄이는 이야기",
        strengths=[
            {"id": str(index), "title": "강점", "description": "무선 청소"}
            for index in range(3)
        ],
        problems=[{"heading": str(index), "body": "서로 다른 불편"} for index in range(4)],
    )


def project_input():
    return ProjectInput.model_validate(json.loads(Path("tests/fixtures/appliance.json").read_text()))


def test_format_feedback_recovers_and_retry_is_bounded():
    seen = []

    def generate(prompt, schema):
        seen.append(prompt)
        return "invalid" if len(seen) == 1 else review().model_dump_json()

    result = format_graph(Review.model_validate, generator=generate).invoke(
        {"prompt": "입력", "schema": {}, "attempt": 0}
    )
    assert result["attempt"] == 2 and not result["failed"]
    assert "출력 형식 오류" in seen[1]

    failed = format_graph(Review.model_validate, generator=lambda *_: "{}").invoke(
        {"prompt": "", "schema": {}, "attempt": 0}
    )
    assert failed["attempt"] == 3 and failed["failed"]


def test_provider_retry_is_limited_to_transient_errors():
    class Busy(Exception):
        code = 429

    calls = []

    def call():
        calls.append(1)
        if len(calls) < 3:
            raise Busy()
        return "ok"

    waits = []
    assert transient_call(call, sleeper=waits.append) == "ok"
    assert waits == [15, 30]

    class Forbidden(Exception):
        code = 403

    with pytest.raises(Forbidden):
        transient_call(
            lambda: (_ for _ in []).throw(Forbidden()),
            sleeper=lambda _: pytest.fail("must not retry"),
        )


def test_template_uses_price_only_and_never_exposes_quantity_as_product_count():
    scene, fixed = plan(project_input(), review())
    assert [block["id"] for block in scene["blocks"]] == [
        "hero",
        "problem",
        "transition",
        "product-visual",
        "positioning",
        "product-gallery",
        "comparison",
        "promise",
        "benefit-1",
        "benefit-2",
        "benefit-3",
        "rewards",
    ]
    reward_nodes = {node["id"]: node for node in scene["blocks"][-1]["nodes"]}
    template_nodes = {node["id"]: node for node in CATALOG["rewards"]["nodes"]}
    assert reward_nodes.keys() == template_nodes.keys()
    assert not any(node_id.startswith("rewards.normal-") for node_id in reward_nodes)
    assert not any(node_id.startswith("rewards.sale-") for node_id in reward_nodes)
    assert not any(node_id.startswith("rewards.normal-") for node_id in fixed)
    assert fixed["rewards.price-label-0"] == "가격"
    assert fixed["rewards.price-value-0"] == "149,000원"
    assert reward_nodes["rewards.price-label-bg-0"]["y"] == 775
    assert reward_nodes["rewards.price-divider-0"]["fill"] == "brand-light"
    serialized = json.dumps(scene, ensure_ascii=False)
    assert "100개" not in serialized


def test_reward_name_is_vertically_centered_above_image_midline_and_badge_is_centered():
    nodes = {node["id"]: node for node in CATALOG["rewards"]["nodes"]}
    for index in range(3):
        image = nodes[f"rewards.image-{index}"]
        name = nodes[f"rewards.name-{index}"]
        divider = nodes[f"rewards.price-divider-{index}"]
        background = nodes[f"rewards.price-label-bg-{index}"]
        label = nodes[f"rewards.price-label-{index}"]
        card = nodes[f"rewards.card-{index}"]
        value = nodes[f"rewards.price-value-{index}"]

        midline = divider["y"] + divider["height"] / 2
        assert midline == image["y"] + image["height"] / 2
        assert abs(name["y"] + name["height"] / 2 - (image["y"] + midline) / 2) <= 0.5
        assert (name["x"], name["align"]) == (divider["x"], "left")
        assert label["y"] - (divider["y"] + divider["height"]) == 64
        assert (label["x"], label["width"], label["align"]) == (
            background["x"], background["width"], "center"
        )
        assert value["y"] + value["height"] <= card["y"] + card["height"]


def test_generation_requirements_preserve_block_categories_and_all_slots():
    scene, fixed = plan(project_input(), review())
    categories = TEMPLATE["blockLibrary"]["categories"]
    assert len(CATEGORIES) == len(categories)
    for block in scene["blocks"]:
        assert block["categoryIds"] and set(block["categoryIds"]) <= set(CATEGORIES)

    by_id = {item["id"]: item for item in requirements(scene, fixed)}
    assert by_id["hero"]["category_ids"] == ["main-visual"]
    assert by_id["rewards"]["category_ids"] == ["purchase-option"]
    assert all("reference" not in text for block in by_id.values() for text in block["texts"])

    with pytest.raises(ValueError, match="슬롯 ID"):
        validate_copy(CopyResult(texts={}, image_prompts={}, summary="", storyline=""), scene, fixed)


def test_generation_plan_allows_a_project_without_source_images():
    project = project_input().model_copy(update={"source_images": []})
    scene, fixed = plan(project, review())
    assert scene["blocks"]
    assert fixed["rewards.price-value-0"] == "149,000원"


@pytest.mark.parametrize("count", [1, 2, 3])
def test_reward_cards_keep_placeholders_without_normal_price_row(count):
    project = project_input().model_copy(update={"rewards": project_input().rewards[:count]})
    scene, fixed = plan(project, review())
    nodes = {node["id"]: node for node in scene["blocks"][-1]["nodes"]}
    assert not any(node_id.startswith("rewards.normal-") for node_id in nodes)
    assert not any(node_id.startswith("rewards.normal-") for node_id in fixed)
    assert not any(node_id.startswith("rewards.sale-") for node_id in nodes)
    assert [fixed[f"rewards.name-{index}"] for index in range(count, 3)] == [
        "미등록 선물"
    ] * (3 - count)
    assert [fixed[f"rewards.price-value-{index}"] for index in range(count, 3)] == [
        "—"
    ] * (3 - count)


def test_non_appliance_copy_requirements_do_not_contain_vacuum_template_facts():
    project = project_input()
    project = project.model_copy(
        update={
            "title": "수제 쿠키",
            "category": "식품 / 과자",
            "rewards": [
                project.rewards[0].model_copy(
                    update={"name": "쿠키 한 상자", "description": "쿠키 12개", "price": 12000}
                )
            ],
        }
    )
    review = Review(
        reply="확인",
        product="수제 쿠키",
        story="가족과 나누는 간식",
        strengths=[
            {"id": str(index), "title": title, "description": "입력된 재료와 포장 정보"}
            for index, title in enumerate(("신선한 재료", "다양한 맛", "편리한 포장"))
        ],
        problems=[{"heading": str(index), "body": "간식 선택의 어려움"} for index in range(4)],
    )
    scene, fixed = plan(project, review)
    points = [block for block in scene["blocks"] if block["id"].startswith("benefit-")]
    assert len({block["templateBlockId"] for block in points}) == 3
    prompt_slots = json.dumps(requirements(scene, fixed), ensure_ascii=False)
    assert all(word not in prompt_slots for word in ("청소", "먼지통", "브러시", "무선"))
    assert "신선한 재료" in prompt_slots
    assert "쿠키 한 상자" in prompt_slots


def test_removed_legacy_fields_are_rejected_at_the_dto_boundary():
    raw = json.loads(Path("tests/fixtures/appliance.json").read_text())
    raw["normal_price"] = 199_000
    with pytest.raises(ValidationError, match="normal_price"):
        ProjectInput.model_validate(raw)

    reward = raw["rewards"][0]
    reward["product_count"] = 1
    with pytest.raises(ValidationError, match="product_count"):
        ProjectInput.model_validate(raw)


def test_completion_contract_rejects_cross_status_slot_conflicts():
    image = SuccessfulImage(
        slot_id="hero",
        file_url="https://cdn.example.com/hero.png",
        content_type="image/png",
        file_size=100,
        width=860,
        height=1200,
    )
    body = GeneratedBody(
        cover_image_slot_id="hero",
        intro_content=[ImageContentBlock(type="IMAGE", slot_id="hero")],
    )
    RunCompletionRequest(
        status="succeeded",
        generated_body=body,
        successful_images=[image],
        failed_slots=[],
        error=None,
    )

    with pytest.raises(ValidationError, match="동시에"):
        RunCompletionRequest(
            status="partially_succeeded",
            generated_body=body,
            successful_images=[image],
            failed_slots=[
                FailedSlot(
                    slot_id="hero",
                    stage="upload",
                    error=AsyncError(
                        code="IMAGE_UPLOAD_FAILED",
                        message="업로드 실패",
                        retryable=True,
                        detail=None,
                    ),
                )
            ],
            error=None,
        )
