"""Deterministic, escaped lower-page HTML on the existing BE IMAGE/TEXT boundary."""

from html import escape

from .models import (
    GeneratedBody,
    ImageContentBlock,
    StoryContext,
    SuccessfulImage,
    TextContentBlock,
)

SECTIONS = (
    ("budget", "프로젝트 예산"),
    ("schedule", "프로젝트 일정"),
    ("team", "프로젝트 팀 소개"),
    ("policy", "프로젝트 정책"),
    ("risks", "예상되는 어려움"),
)
SECTION_TITLE_STYLE = "border-left:3px solid #202124;padding-left:10px;margin:0 0 24px;font-size:18px;line-height:1.45"
SUBTITLE_STYLE = "margin:0 0 16px;font-size:16px;line-height:1.5"
DIVIDER_STYLE = "border:0;border-top:1px solid #e6e6e6;margin:36px 0"
FUNDING_NOTICE = (
    (
        "펀딩은 계획의 실현을 함께 지원하는 과정입니다.",
        "프로젝트 내용은 현재의 제작·전달 계획을 바탕으로 합니다. 실제 진행 과정에서 일정이나 결과가 달라질 수 있습니다.",
    ),
    (
        "진행 중 변경 사항을 확인해 주세요.",
        "프로젝트의 일정·구성에 변경이 생기면 창작자의 공지와 프로젝트 안내에서 확인해 주세요.",
    ),
)


def context_summary(context: StoryContext) -> str:
    """Show the exact lower-page facts in the chat before revision confirmation."""
    return "\n\n".join(f"{label}\n{value}" for key, label in SECTIONS if (value := getattr(context, key)))


def _paragraphs(value: str) -> str:
    # Never accept user/model HTML. All markup belongs to this renderer and the BE allowlist.
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    return "".join(
        "<p>" + escape(paragraph.strip()).replace("\n", "<br>") + "</p>"
        for paragraph in normalized.split("\n\n")
        if paragraph.strip()
    )


def _heading(label: str, *, level: int) -> str:
    tag = "h2" if level == 2 else "h3"
    style = SECTION_TITLE_STYLE if level == 2 else SUBTITLE_STYLE
    return f'<{tag} style="{style}">' + escape(label) + f"</{tag}>"


def _divider() -> str:
    return f'<hr style="{DIVIDER_STYLE}">'


def _section(title: str, body: str) -> str:
    return "<section>" + _heading(title, level=2) + body + "</section>"


def _safety_html(context: StoryContext) -> str:
    parts = [_heading("크라우드 펀딩에 대한 안내", level=3)]
    for title, description in FUNDING_NOTICE:
        parts.extend(("<p><strong>" + escape(title) + "</strong></p>", _paragraphs(description)))
    if context.policy or context.risks:
        parts.append(_divider())
    for key, label in SECTIONS[3:]:
        if value := getattr(context, key):
            parts.extend((_heading(label, level=3), _paragraphs(value)))
    return _section("신뢰와 안전", "".join(parts))


def information_html(context: StoryContext) -> str:
    parts = []
    for key, label in SECTIONS[:3]:
        if value := getattr(context, key):
            parts.append(_section(label, _paragraphs(value)))
    parts.append(_safety_html(context))
    return _divider().join(parts)


def generated_body(images: list[SuccessfulImage], context: StoryContext) -> GeneratedBody:
    if not images:
        raise ValueError("상세페이지에는 성공한 PNG가 필요합니다.")
    html = information_html(context)
    content: list[TextContentBlock | ImageContentBlock] = [
        ImageContentBlock(type="IMAGE", slot_id=image.slot_id) for image in images
    ]
    content.append(TextContentBlock(type="TEXT", value=html))
    return GeneratedBody(
        cover_image_slot_id=images[0].slot_id,
        intro_content=content,
    )
