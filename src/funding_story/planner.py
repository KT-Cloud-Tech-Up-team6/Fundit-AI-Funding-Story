import copy
import json
from pathlib import Path

from .models import ProjectInput, Review

TEMPLATE = json.loads((Path(__file__).parent / "resources/template.json").read_text())
COMPOSITION = TEMPLATE["composition"]
CATALOG = {b["id"]: b for b in TEMPLATE["scene"]["blocks"]}
CATEGORIES = {category["id"]: category for category in TEMPLATE["blockLibrary"]["categories"]}


def plan(project: ProjectInput, review: Review):
    if (
        len(review.problems) != 4
        or not COMPOSITION["point_min"] <= len(review.strengths) <= COMPOSITION["point_max"]
        or not project.rewards
    ):
        raise ValueError("불편한 상황 4개, 핵심 강점 3개 이상과 리워드를 확인해 주세요.")
    blocks = [copy.deepcopy(CATALOG[key]) for key in COMPOSITION["required_before_points"]]
    for index, strength in enumerate(review.strengths):
        desc = strength.title + strength.description
        name = (
            "feature-wireless"
            if "무선" in desc
            else "feature-dustbin"
            if "먼지통" in desc
            else "feature-handling"
            if "조작" in desc
            else "feature-ergonomic"
            if "허리" in desc or "스틱" in desc
            else "feature-brush"
            if "브러시" in desc or "헤드" in desc
            else "feature-slim"
        )
        block = copy.deepcopy(CATALOG[name])
        block["templateBlockId"] = name
        block["strengthId"] = strength.id
        block["id"] = f"benefit-{index + 1}"
        block["label"] = f"Point {index + 1:02d} · {strength.title}"
        for node in block["nodes"]:
            node["id"] = node["id"].replace(name + ".", block["id"] + ".")
            if node["id"].endswith(".point"):
                node["text"] = f"Point {index + 1:02d}"
        blocks.append(block)
    blocks.extend(copy.deepcopy(CATALOG[key]) for key in COMPOSITION["required_after_points"])
    fixed = {
        n["id"]: n["text"]
        for b in blocks
        for n in b["nodes"]
        if n["kind"] == "text" and n["text"] in ("×", "✓")
    }
    for i in range(3):
        fixed[f"hero.point-{i}"] = f"Point {i + 1:02d}"
    for i in range(len(review.strengths)):
        fixed[f"benefit-{i + 1}.point"] = f"Point {i + 1:02d}"
    for i in range(3):
        reward = project.rewards[i] if i < len(project.rewards) else None
        for role, value in {
            "name": reward.name if reward else "미등록 선물",
            "normal-label": "",
            "sale-label": "가격",
            "normal-price": "",
            "sale-price": f"{reward.price:,}원" if reward else "—",
        }.items():
            fixed[f"rewards.{role}-{i}"] = value
    for block in blocks:
        for node in block["nodes"]:
            if node["kind"] == "text" and node["id"] in fixed:
                node["text"] = fixed[node["id"]]
            if node["kind"] == "image":
                node.pop("sourceCrop", None)
                node.pop("zoom", None)
                node["assetId"] = ""
                node["pending"] = True
    scene = {
        **TEMPLATE["scene"],
        "brand": project.brand_color,
        "blocks": blocks,
        "compositionVersion": COMPOSITION["version"],
        "textLayoutVersion": 1,
    }
    return scene, fixed


def requirements(scene, fixed):
    return [
        {
            "id": b["id"],
            "role": b["label"],
            "category_ids": b["categoryIds"],
            "strength_id": b.get("strengthId"),
            "texts": [
                {
                    "id": n["id"],
                    "reference": n["text"],
                    "layout": n.get("copyFit", {}),
                    "font_size": n["fontSize"],
                    "width_px": n["width"],
                    "purpose": TEMPLATE["purposes"].get(
                        n["id"].replace(b["id"] + ".", b.get("templateBlockId", b["id"]) + "."),
                        "블록 역할에 맞는 짧은 원고",
                    ),
                    "max_chars": max(
                        8,
                        int(n["width"] / n["fontSize"])
                        * int(n["height"] / (n["fontSize"] * n["lineHeight"])),
                    ),
                }
                for n in b["nodes"]
                if n["kind"] == "text" and n["id"] not in fixed
            ],
            "images": [
                {"id": n["id"], "desc": n["desc"], "width": n["width"], "height": n["height"]}
                for n in b["nodes"]
                if n["kind"] == "image"
            ],
        }
        for b in scene["blocks"]
    ]


def validate_copy(result, scene, fixed):
    text_ids = {
        n["id"] for b in scene["blocks"] for n in b["nodes"] if n["kind"] == "text" and n["id"] not in fixed
    }
    image_ids = {n["id"] for b in scene["blocks"] for n in b["nodes"] if n["kind"] == "image"}
    if set(result.texts) != text_ids or set(result.image_prompts) != image_ids:
        raise ValueError(
            "슬롯 ID를 빠짐없이 정확히 한 번 작성하세요. texts="
            + str(sorted(text_ids))
            + " images="
            + str(sorted(image_ids))
        )
    if any(not v.strip() for v in [*result.texts.values(), *result.image_prompts.values()]):
        raise ValueError("빈 텍스트·이미지 설명은 허용하지 않습니다.")
    from .renderer import measure_scene

    candidate = copy.deepcopy(scene)
    for block in candidate["blocks"]:
        for node in block["nodes"]:
            if node["id"] in result.texts:
                node["text"] = result.texts[node["id"]]
    issues = [issue for block in measure_scene(candidate) for issue in block["issues"]]
    if issues:
        raise ValueError(
            "텍스트 슬롯 형식 오류. 폰트를 줄이지 말고 해당 문구의 길이·줄바꿈을 수정하세요: "
            + json.dumps(issues, ensure_ascii=False)
        )
    return result
