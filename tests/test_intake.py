import pytest
from test_contracts import review

from funding_story.intake import apply_changes
from funding_story.models import ProjectInput, Review
from funding_story.planner import plan


def source():
    return ProjectInput(
        title="무선 청소기",
        rewards=[{"name": "기본 구성", "description": "본체 1개", "price": 169000}],
        asset_ids=["photo"],
    )


def test_explicit_values_reach_fixed_slots_without_mutating_original():
    text = "기본 구성 정상가는 199,000원, 할인가 159,000원, 완제품 수는 2개입니다. 말투는 담백한 해요체. 예산은 부품 확보와 생산에 사용합니다."
    r = review()
    r.input_changes = Review.model_validate(
        {
            "reply": "확인",
            "input_changes": [
                {
                    "field": "normal_price",
                    "reward_index": 0,
                    "value": "199,000원",
                    "quote": "정상가는 199,000원",
                },
                {"field": "price", "reward_index": 0, "value": "159,000원", "quote": "할인가 159,000원"},
                {
                    "field": "product_count",
                    "reward_index": 0,
                    "value": "2개",
                    "quote": "완제품 수는 2개입니다.",
                },
                {"field": "tone", "value": "담백한 해요체", "quote": "말투는 담백한 해요체."},
                {
                    "field": "budget",
                    "value": "부품 확보와 생산에 사용합니다.",
                    "quote": "예산은 부품 확보와 생산에 사용합니다.",
                },
            ],
        }
    ).input_changes
    original = source()
    updated = apply_changes(original, r, text)
    _, fixed = plan(updated, r)
    assert fixed["rewards.sale-price-0"] == "159,000원"
    assert fixed["rewards.normal-price-0"] == "199,000원"
    assert updated.rewards[0].product_count == 2
    assert updated.information["budget"] == "부품 확보와 생산에 사용합니다."
    assert updated.tone == "담백한 해요체"
    assert original.rewards[0].price == 169000 and original.information == {}


@pytest.mark.parametrize(
    "field,value,quote,index",
    [
        ("normal_price", "199000", "정상가는 19만원", 0),
        ("normal_price", "19만원", "정상가는 19만원", 0),
        ("product_count", "1.3kg", "무게는 1.3kg", 0),
        ("price", "100원", "할인가는 100원", 2),
        ("product_count", "0개", "제품은 0개", 0),
    ],
)
def test_inferred_or_invalid_numbers_rejected(field, value, quote, index):
    r = Review(
        reply="확인", input_changes=[{"field": field, "value": value, "quote": quote, "reward_index": index}]
    )
    with pytest.raises(ValueError):
        apply_changes(source(), r, quote)


def test_prior_turn_quote_cannot_silently_reapply():
    r = Review(
        reply="확인",
        input_changes=[
            {"field": "price", "value": "199000원", "quote": "가격은 199000원", "reward_index": 0}
        ],
    )
    with pytest.raises(ValueError):
        apply_changes(source(), r, "강점 순서만 바꿔줘")


def test_no_change_preserves_numbers_and_units():
    original = source()
    original.product_description = "무게 1.3, 사용시간 약 45"
    assert apply_changes(original, Review(reply="확인"), "순서 변경").model_dump() == original.model_dump()
