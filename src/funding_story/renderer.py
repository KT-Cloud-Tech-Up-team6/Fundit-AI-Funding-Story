"""Server PNG export and text metrics use the same Konva browser renderer."""

import base64
import colorsys
import copy
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from playwright.sync_api import sync_playwright

from .config import settings

RESOURCES = Path(__file__).parent / "resources"


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
        Path("data/fonts/PretendardVariable.ttf"),
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return str(candidate)
    raise RuntimeError("Pretendard 폰트가 필요합니다. PRETENDARD_FONT_PATH를 설정하세요.")


@contextmanager
def browser_page():
    with sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 860, "height": 1000}, device_scale_factor=1)
            # Rendering is entirely local; generated text cannot request network resources.
            page.route("**/*", lambda route: route.abort())
            page.set_content('<html><body style="margin:0"><div id="stage"></div></body></html>')
            font = base64.b64encode(Path(_font_path()).read_bytes()).decode()
            page.add_style_tag(
                content="@font-face{font-family:Pretendard;font-style:normal;font-weight:100 900;src:url(data:font/ttf;base64,"
                + font
                + ")}"
            )
            page.add_script_tag(path=str(RESOURCES / "vendor/konva-10.5.0.min.js"))
            page.add_script_tag(path=str(RESOURCES / "konva-renderer.js"))
            page.evaluate(
                "async () => {await Promise.all(['400','500','600','700','800'].map(w=>document.fonts.load(w+' 32px Pretendard','가나다 청소')));await document.fonts.ready;}"
            )
            yield page
        finally:
            browser.close()


def _prepared(scene):
    scene = copy.deepcopy(scene)
    for block in scene["blocks"]:
        for node in block["nodes"]:
            for key in ("fill", "stroke"):
                if key in node:
                    node[key] = _resolve_color(node[key], scene["brand"])
            if node.get("sideBorders"):
                border = node["sideBorders"]
                border["color"] = _resolve_color(border["color"], scene["brand"])
    return scene


def measure_scene(scene):
    """Deterministic slot-format measurement, no model call or auto correction."""
    with browser_page() as page:
        return page.evaluate("scene => measureScene(scene)", _prepared(scene))


def render_scene(scene, sources, block_ids=None):
    scene = _prepared(scene)
    encoded_sources = {}
    for block in scene["blocks"]:
        for node in block["nodes"]:
            aid = node.get("assetId")
            if node["kind"] == "image" and aid and not node.get("pending") and aid not in encoded_sources:
                blob, mime = sources[aid]
                encoded_sources[aid] = (
                    "data:" + mime + ";base64," + base64.b64encode(blob).decode()
                )
    results = []
    with browser_page() as page:
        page.evaluate("sources => {window.sceneSources = sources;}", encoded_sources)
        for block in scene["blocks"]:
            if block_ids is not None and block["id"] not in block_ids:
                continue
            result = page.evaluate("async block => renderBlock(block,window.sceneSources)", block)
            results.append(
                {
                    "block_id": block["id"],
                    "label": block["label"],
                    "width": block["width"],
                    "height": block["height"],
                    "bytes": base64.b64decode(result["png"].split(",", 1)[1]),
                    "layout": result["layout"],
                }
            )
    return results
