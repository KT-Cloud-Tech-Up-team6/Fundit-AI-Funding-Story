import json
from pathlib import Path

import pytest

from funding_story.graph import format_graph
from funding_story.models import CopyResult, ProjectInput, Review
from funding_story.planner import plan, validate_copy
from funding_story.provider import transient_call


def review():
    return Review(
        reply="확인",
        strengths=[{"id": str(i), "title": "강점", "description": "무선 청소"} for i in range(3)],
        problems=[{"heading": str(i), "body": "서로 다른 불편"} for i in range(4)],
    )


def test_format_feedback_recovers():
    seen = []

    def generate(prompt, schema):
        seen.append(prompt)
        return "invalid" if len(seen) == 1 else review().model_dump_json()

    result = format_graph(Review.model_validate, generator=generate).invoke(
        {"prompt": "입력", "schema": {}, "attempt": 0}
    )
    assert result["attempt"] == 2 and not result["failed"]
    assert "출력 형식 오류" in seen[1]


def test_format_retry_is_bounded():
    result = format_graph(Review.model_validate, generator=lambda *_: "{}").invoke(
        {"prompt": "", "schema": {}, "attempt": 0}
    )
    assert result["attempt"] == 3 and result["failed"]


def test_provider_retry_separate():
    class Error(Exception):
        code = 429

    calls = []

    def call():
        calls.append(1)
        if len(calls) < 3:
            raise Error()
        return "ok"

    waits = []
    assert transient_call(call, sleeper=waits.append) == "ok"
    assert waits == [15, 30]


def test_nontransient_not_retried():
    class Error(Exception):
        code = 403

    with pytest.raises(Error):
        transient_call(
            lambda: (_ for _ in []).throw(Error()), sleeper=lambda _: pytest.fail("must not retry")
        )


def test_template_preserved_and_price_bound():
    x = ProjectInput.model_validate(json.loads(Path("tests/fixtures/appliance.json").read_text()))
    x.asset_ids = ["reference"]
    scene, fixed = plan(x, review())
    assert [b["id"] for b in scene["blocks"]] == [
        "hero",
        "problem",
        "benefit-1",
        "benefit-2",
        "benefit-3",
        "rewards",
    ]
    assert len([n for n in scene["blocks"][1]["nodes"] if n["id"].startswith("problem.heading")]) == 4
    assert fixed["rewards.normal-price-0"] == "199,000원"
    assert fixed["rewards.sale-price-0"] == "149,000원"
    assert all(b["width"] == 860 for b in scene["blocks"])
    assert all(
        n["italic"]
        for n in scene["blocks"][0]["nodes"]
        if n["kind"] == "text" and n["id"].startswith("hero.point")
    )
    assert len([n for n in scene["blocks"][-1]["nodes"] if n["kind"] == "image"]) == 3


def test_missing_price_never_inferred():
    x = ProjectInput.model_validate(json.loads(Path("tests/fixtures/appliance.json").read_text()))
    x.asset_ids = ["reference"]
    x.rewards = x.rewards[:1]
    x.rewards[0].normal_price = None
    scene, fixed = plan(x, review())
    assert fixed["rewards.normal-price-0"] == "—" and fixed["rewards.sale-price-1"] == "—"
    assert len([n for n in scene["blocks"][-1]["nodes"] if n["kind"] == "image"]) == 3


def test_image_slot_omission_rejected():
    x = ProjectInput.model_validate(json.loads(Path("tests/fixtures/appliance.json").read_text()))
    x.asset_ids = ["a"]
    scene, fixed = plan(x, review())
    with pytest.raises(ValueError):
        validate_copy(CopyResult(texts={}, image_prompts={}, summary="", storyline=""), scene, fixed)


def test_fourth_reward_rejected():
    x = json.loads(Path("tests/fixtures/appliance.json").read_text())
    x["rewards"].append(x["rewards"][0])
    with pytest.raises(ValueError):
        ProjectInput.model_validate(x)


def test_stream_retry_before_first_token_only():
    from funding_story.provider import stream_with_retry

    class Busy(Exception):
        code = 503

    calls = []

    def chunks():
        calls.append(1)
        if len(calls) == 1:
            raise Busy()
        yield "hello"

    waits = []
    assert list(stream_with_retry(chunks, waits.append)) == ["hello"]
    assert waits == [15]

    def partial():
        yield "partial"
        raise Busy()

    it = stream_with_retry(partial, lambda _: pytest.fail("must not repeat emitted tokens"))
    assert next(it) == "partial"
    with pytest.raises(Busy):
        next(it)
