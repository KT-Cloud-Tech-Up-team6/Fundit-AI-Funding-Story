from html.parser import HTMLParser

import pytest
from pydantic import ValidationError
from test_recovery import context

from funding_story.body import context_summary, generated_body, information_html
from funding_story.models import Review, RewardOption, StoryContext, SuccessfulImage


class Markup(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, attrs))

    def handle_data(self, data):
        self.text.append(data)


def test_lower_body_section_order_and_current_core_reward_facts():
    facts = StoryContext(
        budget="금형 제작과 포장에 사용합니다.",
        schedule="11월 중순 제작\n12월 초 발송",
        team="생활가전 개발팀입니다.",
        policy="불량 발생 시 교환합니다.",
        risks="부품 지연 시 일정을 공지합니다.",
    )
    rewards = context().rewards
    rewards[0].options = [RewardOption(group_name="색상", values=["화이트", "그레이"])]
    html = information_html(facts, rewards)
    headings = [
        "프로젝트 예산",
        "프로젝트 일정",
        "프로젝트 팀 소개",
        "신뢰와 안전",
        "프로젝트 정책",
        "예상되는 어려움",
        "리워드 상세 설명",
    ]
    assert [html.index(label) for label in headings] == sorted(html.index(label) for label in headings)
    assert "11월 중순 제작<br>12월 초 발송" in html
    assert "<p>가격: 129,000원</p>" in html
    assert "<li>색상: 화이트, 그레이</li>" in html
    assert "본품 1대" in html
    assert "목표 금액" not in html
    assert "크라우드 펀딩 안내" not in html
    assert "normal_price" not in html
    assert "quantity" not in html


def test_absent_optional_sections_do_not_fabricate_placeholders():
    html = information_html(StoryContext(), context().rewards)
    assert html.startswith("<p><strong>리워드 상세 설명</strong></p>")
    assert all(text not in html for text in ("예산", "일정", "팀 소개", "신뢰와 안전", "미정", "추후"))
    assert context_summary(StoryContext()) == ""


def test_all_user_and_core_values_are_escaped_with_only_be_supported_markup():
    attack = '<script>alert("x")</script><img src=x onerror=alert(1)>&'
    facts = StoryContext(budget=attack, team="팀\n\n소개")
    reward = context().rewards[0].model_copy(update={"name": attack, "description": attack})
    html = information_html(facts, [reward])
    parsed = Markup()
    parsed.feed(html)
    assert all(tag in ("p", "strong", "br", "ul", "li") and not attrs for tag, attrs in parsed.tags)
    assert "".join(parsed.text).count(attack) == 3
    assert "<p>팀</p><p>소개</p>" in html
    assert "&lt;script&gt;" in html


def test_body_keeps_uploaded_image_order_before_one_html_text_block():
    images = [
        SuccessfulImage(
            slot_id=slot,
            file_url=f"https://cdn.example.com/{slot}.png",
            content_type="image/png",
            file_size=10,
            width=860,
            height=1200,
        )
        for slot in ("hero", "benefit-2", "rewards")
    ]
    body = generated_body(images, StoryContext(budget="제작비"), context().rewards)
    assert body.cover_image_slot_id == "hero"
    assert [block.slot_id for block in body.intro_content[:-1]] == ["hero", "benefit-2", "rewards"]
    assert body.intro_content[-1].value.startswith("<p><strong>프로젝트 예산")
    assert all(
        "file_url" not in block and "html" not in block for block in body.model_dump()["intro_content"]
    )
    with pytest.raises(ValueError, match="PNG"):
        generated_body([], StoryContext(), context().rewards)


def test_review_is_backward_compatible_but_new_context_is_bounded():
    assert Review(reply="확인").story_context == StoryContext()
    with pytest.raises(ValidationError):
        Review(reply="확인", story_context={"budget": "a" * 6001})
    with pytest.raises(ValidationError):
        Review(reply="확인", story_context={"invented_policy": "unknown"})
