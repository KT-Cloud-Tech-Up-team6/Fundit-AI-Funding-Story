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


def _heading(label: str) -> str:
    return "<p><strong>" + escape(label) + "</strong></p>"


def information_html(context: StoryContext) -> str:
    parts = []
    for key, label in SECTIONS[:3]:
        if value := getattr(context, key):
            parts.extend((_heading(label), _paragraphs(value)))
    if context.policy or context.risks:
        parts.append(_heading("신뢰와 안전"))
        for key, label in SECTIONS[3:]:
            if value := getattr(context, key):
                parts.extend((_heading(label), _paragraphs(value)))
    return "".join(parts)


def generated_body(images: list[SuccessfulImage], context: StoryContext) -> GeneratedBody:
    if not images:
        raise ValueError("상세페이지에는 성공한 PNG가 필요합니다.")
    html = information_html(context)
    content: list[TextContentBlock | ImageContentBlock] = [
        ImageContentBlock(type="IMAGE", slot_id=image.slot_id) for image in images
    ]
    if html:
        content.append(TextContentBlock(type="TEXT", value=html))
    return GeneratedBody(
        cover_image_slot_id=images[0].slot_id,
        intro_content=content,
    )
