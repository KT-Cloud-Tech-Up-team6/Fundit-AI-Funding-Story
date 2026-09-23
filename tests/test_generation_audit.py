"""The benchmark must reject incomplete output even when it is fast."""

import copy
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace as Object

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from benchmark_full_generation import completeness, sha


@pytest.fixture
def complete_case():
    expected = {
        "blocks": [
            {
                "id": slot,
                "nodes": [
                    {"id": slot + ".image", "kind": "image"},
                    {"id": slot + ".text", "kind": "text"},
                ],
            }
            for slot in ("hero", "benefit")
        ]
    }
    scene = copy.deepcopy(expected)
    sources = {}
    for index, block in enumerate(scene["blocks"]):
        for node in block["nodes"]:
            if node["kind"] == "image":
                node.update(pending=False, assetId=node["id"])
                output = BytesIO()
                pixels = Image.new("RGB", (16, 16), (index * 100, 20, 50))
                pixels.putpixel((0, 0), (255, 255, 255))
                pixels.save(output, format="PNG")
                sources[node["id"]] = (output.getvalue(), "image/png")
            else:
                node["text"] = "실제 생성 문구"
    draft = Object(
        texts={slot + ".text": "실제 생성 문구" for slot in ("hero", "benefit")},
        image_prompts={slot: "설명" for slot in sources},
    )
    body = Object(
        status="succeeded",
        failed_slots=[],
        successful_images=[Object(slot_id=slot) for slot in ("hero", "benefit")],
        generated_body=Object(
            intro_content=[Object(type="IMAGE", slot_id=slot) for slot in ("hero", "benefit")]
            + [Object(type="TEXT", value="<p>추가 본문</p>")]
        ),
    )
    return {
        "expected": expected,
        "scene": scene,
        "fixed": {},
        "draft": draft,
        "sources": sources,
        "body": body,
        "uploaded": {"hero": {}, "benefit": {}},
        "model_hashes": [sha(v[0]) for v in sources.values()],
        "reference_hashes": [],
    }


def test_complete_output_passes(complete_case):
    assert completeness(**complete_case)["passed"]


@pytest.mark.parametrize(
    "problem", ["partial", "image", "text", "placeholder", "png", "html", "reuse", "blank", "region"]
)
def test_incomplete_or_reused_results_cannot_meet_goal(complete_case, problem):
    case = complete_case
    if problem == "partial":
        case["body"].status = "partially_succeeded"
    elif problem == "image":
        case["sources"].pop("hero.image")
    elif problem == "text":
        case["draft"].texts["hero.text"] = ""
    elif problem == "placeholder":
        case["scene"]["blocks"][0]["nodes"][0]["pending"] = True
    elif problem == "png":
        case["uploaded"].pop("hero")
    elif problem == "html":
        case["body"].generated_body = None
    elif problem == "reuse":
        case["reference_hashes"] = [case["model_hashes"][0]]
    elif problem == "blank":
        output = BytesIO()
        Image.new("RGB", (16, 16), "white").save(output, format="PNG")
        case["sources"]["hero.image"] = (output.getvalue(), "image/png")
        case["model_hashes"] = [sha(v[0]) for v in case["sources"].values()]
    elif problem == "region":
        case["scene"]["blocks"][0]["nodes"].pop()
    assert not completeness(**case)["passed"]
