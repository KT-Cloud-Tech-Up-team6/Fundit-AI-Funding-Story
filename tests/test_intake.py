import pytest
from pydantic import ValidationError

from funding_story.intake import parse_initial_review, parse_review


def test_review_parser_accepts_only_the_current_summary_contract():
    raw = {
        "reply": "요약을 확인해 주세요.",
        "product": "작은 공간용 공기청정기",
        "story": "소음 부담을 줄인 제작 이야기",
        "strengths": [
            {"id": "quiet", "title": "저소음", "description": "생활 공간에서 부담을 줄입니다."}
        ],
        "problems": [],
        "missing": ["주요 사용 장면"],
        "tone": None,
        "brand_color": None,
    }
    assert parse_review(raw) == parse_initial_review(raw)


@pytest.mark.parametrize("removed", ["input_changes", "information", "include_information"])
def test_removed_intake_fields_are_rejected(removed):
    with pytest.raises(ValidationError, match=removed):
        parse_review({"reply": "확인", removed: [] if removed == "input_changes" else {}})
