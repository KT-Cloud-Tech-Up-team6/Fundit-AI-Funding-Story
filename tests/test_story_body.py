from html.parser import HTMLParser

import pytest
from pydantic import ValidationError

from funding_story.body import context_summary, generated_body, information_html
from funding_story.models import Review, StoryContext, SuccessfulImage


class Markup(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, attrs))

    def handle_data(self, data):
        self.text.append(data)


def test_lower_body_section_order_without_reward_details():
    facts = StoryContext(
        budget="금형 제작과 포장에 사용합니다.",
        schedule="11월 중순 제작\n12월 초 발송",
        team="생활가전 개발팀입니다.",
        policy="불량 발생 시 교환합니다.",
        risks="부품 지연 시 일정을 공지합니다.",
    )
    html = information_html(facts)
    headings = [
        "프로젝트 예산",
        "프로젝트 일정",
        "프로젝트 팀 소개",
        "신뢰와 안전",
        "크라우드 펀딩에 대한 안내",
        "프로젝트 정책",
        "예상되는 어려움",
    ]
    assert [html.index(label) for label in headings] == sorted(html.index(label) for label in headings)
    assert "11월 중순 제작<br>12월 초 발송" in html
    assert "리워드 상세 설명" not in html
    assert "기본 리워드" not in html
    assert "본품 1대" not in html
    assert "129,000원" not in html
    assert "목표 금액" not in html
    assert html.count("<h2 ") == 4
    assert html.count("<hr ") == 4
    assert "펀딩은 계획의 실현을 함께 지원하는 과정입니다." in html
    assert "border-left:3px solid" in html
    assert "normal_price" not in html
    assert "quantity" not in html


def test_absent_optional_sections_do_not_fabricate_placeholders():
    html = information_html(StoryContext())
    assert "<h2" in html and "신뢰와 안전" in html
    assert "크라우드 펀딩에 대한 안내" in html
    assert "프로젝트 예산" not in html and "프로젝트 정책" not in html
    assert context_summary(StoryContext()) == ""


def test_all_user_values_are_escaped_and_styles_are_static():
    attack = '<script>alert("x")</script><img src=x onerror=alert(1)>&'
    facts = StoryContext(budget=attack, team="팀\n\n소개", policy=attack, risks=attack)
    html = information_html(facts)
    parsed = Markup()
    parsed.feed(html)
    assert all(tag in ("section", "h2", "h3", "hr", "p", "strong", "br") for tag, _ in parsed.tags)
    assert all(
        not attrs if tag in ("section", "p", "strong", "br") else len(attrs) == 1 and attrs[0][0] == "style"
        for tag, attrs in parsed.tags
    )
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
    body = generated_body(images, StoryContext(budget="제작비"))
    assert body.cover_image_slot_id == "hero"
    assert [block.slot_id for block in body.intro_content[:-1]] == ["hero", "benefit-2", "rewards"]
    assert body.intro_content[-1].value.startswith("<section><h2")
    assert "리워드 상세 설명" not in body.intro_content[-1].value
    assert "본품 1대" not in body.intro_content[-1].value
    assert all(
        "file_url" not in block and "html" not in block for block in body.model_dump()["intro_content"]
    )
    with pytest.raises(ValueError, match="PNG"):
        generated_body([], StoryContext())


def test_body_with_no_optional_information_keeps_fixed_safety_notice():
    image = SuccessfulImage(
        slot_id="hero",
        file_url="https://cdn.example.com/hero.png",
        content_type="image/png",
        file_size=10,
        width=860,
        height=1200,
    )
    body = generated_body([image], StoryContext())
    assert [block.type for block in body.intro_content] == ["IMAGE", "TEXT"]
    assert "크라우드 펀딩에 대한 안내" in body.intro_content[-1].value


def test_review_is_backward_compatible_but_new_context_is_bounded():
    assert Review(reply="확인").story_context == StoryContext()
    with pytest.raises(ValidationError):
        Review(reply="확인", story_context={"budget": "a" * 6001})
    with pytest.raises(ValidationError):
        Review(reply="확인", story_context={"invented_policy": "unknown"})
