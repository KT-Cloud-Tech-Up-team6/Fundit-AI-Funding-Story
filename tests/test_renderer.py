import copy
import io

from PIL import Image

from funding_story.planner import CATALOG
from funding_story.renderer import measure_scene, render_scene


def scene(block):
    return {"brand": "#647895", "blocks": [block]}


def promise():
    block = copy.deepcopy(CATALOG["promise"])
    for node in block["nodes"]:
        if node["kind"] == "image":
            node.update(pending=True, assetId="")
    return block


def test_inline_short_and_long_copy_keeps_font_and_gap():
    block = promise()
    emphasis = next(n for n in block["nodes"] if n["id"] == "promise.title-emphasis")
    for text in ("LUMI S1", "슬림 청소기"):
        emphasis["text"] = text
        result = measure_scene(scene(block))[0]
        nodes = {n["id"]: n for n in result["metrics"]}
        a, b = nodes["promise.title-emphasis"], nodes["promise.title-tail"]
        assert abs(b["x"] - a["x"] - a["width"] - 4) < 0.01
        assert a["y"] == b["y"] == 280
        assert a["fontSize"] == b["fontSize"] == emphasis["fontSize"]


def test_actual_wrap_detects_three_lines_without_shrinking():
    block = promise()
    node = next(n for n in block["nodes"] if n["id"] == "promise.point-1")
    node["text"] = "선 없는\n자유로운 이동"
    result = measure_scene(scene(block))[0]
    assert any(
        i["id"] == node["id"] and i["reason"] == "lines" and i["actual"] == 3 for i in result["issues"]
    )
    node["text"] = "선 없는\n자유 이동"
    assert not measure_scene(scene(block))[0]["issues"]
    assert node["fontSize"] == 36


def test_konva_exports_real_png_at_template_size():
    block = promise()
    node = next(n for n in block["nodes"] if n["id"] == "promise.title-emphasis")
    node["text"] = "1.35kg"
    result = render_scene(scene(block), "unused-without-images")[0]
    image = Image.open(io.BytesIO(result["bytes"]))
    assert image.size == (860, 1724)
    assert image.getbbox()
    assert result["layout"]["metrics"]
