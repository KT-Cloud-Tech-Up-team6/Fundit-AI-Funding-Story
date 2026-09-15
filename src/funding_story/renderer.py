import colorsys
import io
import re
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import assets
from .config import settings


def _rgb(value):
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def _hex(rgb):
    return "#" + "".join(f"{round(value * 255):02x}" for value in rgb)


def _resolve_color(value, brand):
    if value == "brand":
        return brand
    reference = "#2457C5" if value == "brand-dark" else "#75B7FF" if value == "brand-light" else None
    if not reference:
        return value
    if brand.lower() == "#4d8fff":
        return reference
    base = colorsys.rgb_to_hls(*[channel / 255 for channel in _rgb("#4D8FFF")])
    tone = colorsys.rgb_to_hls(*[channel / 255 for channel in _rgb(reference)])
    chosen = colorsys.rgb_to_hls(*[channel / 255 for channel in _rgb(brand)])
    hue = (chosen[0] + tone[0] - base[0]) % 1
    lightness = max(0, min(1, chosen[1] + tone[1] - base[1]))
    saturation = max(0, min(1, chosen[2] + tone[2] - base[2]))
    return _hex(colorsys.hls_to_rgb(hue, lightness, saturation))


@lru_cache
def _font_path():
    configured = settings().pretendard_font_path
    candidates = [
        Path(configured) if configured else None,
        Path("/app/fonts/PretendardVariable.ttf"),
        Path("/usr/share/fonts/truetype/pretendard/PretendardVariable.ttf"),
        Path(__file__).parent / "resources/PretendardVariable.ttf",
        Path.cwd().parent.parent
        / "funding-story-ai/node_modules/pretendard/dist/public/variable/PretendardVariable.ttf",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return str(candidate)
    # Pillow images used by contract tests do not assert typography. Production images must set
    # PRETENDARD_FONT_PATH or use the Docker image, which installs the pinned font below.
    return "DejaVuSans.ttf"


@lru_cache(maxsize=128)
def _font(size, weight):
    font = ImageFont.truetype(_font_path(), max(1, round(size)))
    try:
        font.set_variation_by_axes([int(weight)])
    except (AttributeError, OSError, ValueError):
        pass
    return font


def _text_width(draw, value, font, spacing):
    if not value:
        return 0
    return draw.textlength(value, font=font) + spacing * (len(value) - 1)


def _wrap(draw, text, font, spacing, width, mode):
    lines = []
    for paragraph in text.split("\n"):
        if mode == "none" or _text_width(draw, paragraph, font, spacing) <= width:
            lines.append(paragraph)
            continue
        current = ""
        tokens = re.findall(r"\S+\s*", paragraph) or [""]
        for token in tokens:
            if current and _text_width(draw, current + token, font, spacing) > width:
                lines.append(current.rstrip())
                current = ""
            if _text_width(draw, token, font, spacing) <= width:
                current += token
                continue
            for char in token:
                if current and _text_width(draw, current + char, font, spacing) > width:
                    lines.append(current.rstrip())
                    current = ""
                current += char
        lines.append(current.rstrip())
    return lines or [""]


def _draw_spaced(draw, xy, text, font, fill, spacing):
    x, y = xy
    for char in text:
        draw.text((x, y), char, font=font, fill=fill, anchor="lt")
        x += draw.textlength(char, font=font) + spacing


def _draw_text(canvas, node, color, brand):
    width, height = round(node["width"]), round(node["height"])
    padding = round(node.get("sideBorders", {}).get("paddingX", 0))
    content_width = width - padding * 2
    font = _font(node["fontSize"], node["fontWeight"])
    spacing = node.get("letterSpacing", 0)
    line_height = node["fontSize"] * node["lineHeight"]
    scratch = Image.new("RGBA", (max(1, width + 64), max(1, height)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(scratch)
    lines = _wrap(draw, node["text"], font, spacing, content_width, node.get("wrap", "word"))
    total_height = line_height * len(lines)
    vertical = node.get("verticalAlign", "top")
    top = 0 if vertical == "top" else (height - total_height) / 2 if vertical == "middle" else height - total_height
    for index, line in enumerate(lines):
        natural = _text_width(draw, line, font, spacing)
        align = node["align"]
        left = padding if align == "left" else padding + (content_width - natural) / 2
        if align == "right":
            left = padding + content_width - natural
        _draw_spaced(draw, (left, top + index * line_height), line, font, color, spacing)
    if node.get("italic"):
        shear = 0.18
        scratch = scratch.transform(
            scratch.size,
            Image.Transform.AFFINE,
            (1, shear, -shear * height, 0, 1, 0),
            resample=Image.Resampling.BICUBIC,
        )
    x, y = round(node["x"]), round(node["y"])
    canvas.alpha_composite(scratch.crop((0, 0, width, height)), (x, y))
    borders = node.get("sideBorders")
    if borders:
        draw = ImageDraw.Draw(canvas)
        inset = borders["insetY"]
        border_color = _resolve_color(borders["color"], brand)
        draw.line((x, y + inset, x, y + height - inset), fill=border_color, width=round(borders["width"]))
        draw.line(
            (x + width, y + inset, x + width, y + height - inset),
            fill=border_color,
            width=round(borders["width"]),
        )
def _draw_image(canvas, node, project):
    if node.get("pending") or not node.get("assetId"):
        raise ValueError("처리되지 않은 이미지 슬롯: " + node["id"])
    blob, _ = assets.read(node["assetId"], project)
    source = Image.open(io.BytesIO(blob)).convert("RGBA")
    crop = node.get("sourceCrop")
    if crop:
        original_width, original_height = source.size
        left = round(crop["x"] * original_width)
        top = round(crop["y"] * original_height)
        source = source.crop(
            (
                left,
                top,
                left + round(crop["width"] * original_width),
                top + round(crop["height"] * original_height),
            )
        )
    frame_width, frame_height = round(node["width"]), round(node["height"])
    scale = (
        min(frame_width / source.width, frame_height / source.height)
        if node["fit"] == "contain"
        else max(frame_width / source.width, frame_height / source.height)
    ) * node.get("zoom", 1)
    resized = source.resize(
        (max(1, round(source.width * scale)), max(1, round(source.height * scale))),
        Image.Resampling.LANCZOS,
    )
    left = round((frame_width - resized.width) * node["focalX"])
    top = round((frame_height - resized.height) * node["focalY"])
    frame = Image.new("RGBA", (frame_width, frame_height), (0, 0, 0, 0))
    frame.alpha_composite(resized, (left, top))
    radius = min(round(node.get("radius", 0)), frame_width // 2, frame_height // 2)
    if radius:
        mask = Image.new("L", frame.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, frame_width, frame_height), radius=radius, fill=255)
        frame.putalpha(Image.composite(frame.getchannel("A"), Image.new("L", frame.size, 0), mask))
    canvas.alpha_composite(frame, (round(node["x"]), round(node["y"])))


def _draw_node(canvas, node, project, brand):
    color = _resolve_color(node.get("fill", "#000000"), brand)
    if node["kind"] == "image":
        _draw_image(canvas, node, project)
        return
    if node["kind"] == "text":
        _draw_text(canvas, node, color, brand)
        return
    draw = ImageDraw.Draw(canvas)
    x, y = node["x"], node["y"]
    box = (x, y, x + node["width"], y + node["height"])
    if node["kind"] == "line":
        points = [(x + node["points"][i], y + node["points"][i + 1]) for i in range(0, len(node["points"]), 2)]
        if node["closed"]:
            draw.polygon(points, fill=color)
        else:
            draw.line(points, fill=color, width=max(1, round(node["strokeWidth"])))
        return
    if node.get("fadeToTransparent"):
        base = Image.new("RGBA", (max(1, round(node["width"])), max(1, round(node["height"]))), _rgb(color) + (0,))
        alpha = Image.new("L", (1, base.height))
        alpha.putdata([round(255 * (1 - index / max(1, base.height - 1))) for index in range(base.height)])
        base.putalpha(alpha.resize(base.size))
        canvas.alpha_composite(base, (round(x), round(y)))
        return
    stroke = _resolve_color(node["stroke"], brand) if node.get("stroke") else None
    draw.rounded_rectangle(
        box,
        radius=node.get("radius", 0),
        fill=color,
        outline=stroke,
        width=round(node.get("strokeWidth", 0)),
    )


def render_scene(scene, project):
    results = []
    brand = scene["brand"]
    for block in scene["blocks"]:
        canvas = Image.new("RGBA", (round(block["width"]), round(block["height"])), (255, 255, 255, 255))
        for node in block["nodes"]:
            _draw_node(canvas, node, project, brand)
        output = io.BytesIO()
        canvas.convert("RGB").save(output, format="PNG", optimize=True)
        results.append(
            {
                "block_id": block["id"],
                "label": block["label"],
                "width": block["width"],
                "height": block["height"],
                "bytes": output.getvalue(),
            }
        )
    return results
