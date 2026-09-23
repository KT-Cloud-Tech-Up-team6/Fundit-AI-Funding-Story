import copy
import json
from pathlib import Path

from .models import ProjectInput, Review

TEMPLATE = json.loads((Path(__file__).parent / "resources/template.json").read_text())
COMPOSITION = TEMPLATE["composition"]
CATALOG = {b["id"]: b for b in TEMPLATE["scene"]["blocks"]}
CATEGORIES = {category["id"]: category for category in TEMPLATE["blockLibrary"]["categories"]}
POINT_LAYOUTS = (
    "feature-wireless",
    "feature-slim",
    "feature-handling",
    "feature-dustbin",
    "feature-ergonomic",
    "feature-brush",
)


def _image_description(block, node, project, review):
    block_id = block["id"]
    if block_id.startswith("benefit-"):
        strength = review.strengths[int(block_id.rsplit("-", 1)[1]) - 1]
        return f"제품의 강점 '{strength.title}'을 보여주는 장면. 확인된 내용: {strength.description}"
    if block_id == "rewards":
        index = int(node["id"].rsplit("-", 1)[1])
        if index < len(project.rewards):
            reward = project.rewards[index]
            return f"등록된 선물 '{reward.name}'의 구성 이미지. 구성 설명: {reward.description}"
        return "미등록 선물의 빈 이미지 영역"
    descriptions = {
        "hero.image": "입력된 제품의 형태와 용도를 보여주는 대표 이미지",
        "problem.image": "확인된 일상 불편을 보여주는 장면. 특정 경쟁 제품은 만들지 않음",
        "product-visual.image": "입력된 제품의 형태를 보여주는 단독 이미지",
        "positioning.image": "입력된 제품을 실제 사용 맥락에 배치한 이미지",
        "product-gallery.top-left": "입력된 제품의 전체 형태",
        "product-gallery.top-right": "입력된 제품의 다른 각도",
        "product-gallery.bottom-left": "입력된 제품의 사용 장면",
        "product-gallery.bottom-right": "입력된 제품의 확인된 세부 특징",
        "comparison.left-image": "확인된 사용 전 불편 상황. 경쟁 제품이나 성능 수치를 만들지 않음",
        "comparison.right-image": "입력된 제품으로 해결하는 사용 상황. 확인되지 않은 성능은 표현하지 않음",
        "promise.image": "입력된 제품과 확인된 핵심 강점을 함께 보여주는 이미지",
    }
    return descriptions[node["id"]]


def plan(project: ProjectInput, review: Review):
    if (
        len(review.problems) != 4
        or not COMPOSITION["point_min"] <= len(review.strengths) <= COMPOSITION["point_max"]
        or not project.rewards
    ):
        raise ValueError("불편한 상황 4개, 핵심 강점 3개 이상과 리워드를 확인해 주세요.")
    blocks = [copy.deepcopy(CATALOG[key]) for key in COMPOSITION["required_before_points"]]
    for index, strength in enumerate(review.strengths):
        name = POINT_LAYOUTS[index % len(POINT_LAYOUTS)]
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
    for block in blocks:
        if block["id"] == "positioning":
            block["label"] = "제품 가치"
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
            "price-label": "가격",
            "price-value": f"{reward.price:,}원" if reward else "—",
        }.items():
            fixed[f"rewards.{role}-{i}"] = value
    for block in blocks:
        for node in block["nodes"]:
            if node["kind"] == "text" and node["id"] in fixed:
                node["text"] = fixed[node["id"]]
            if node["kind"] == "image":
                node.pop("sourceCrop", None)
                node.pop("zoom", None)
                node["desc"] = _image_description(block, node, project, review)
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
                    "reference_lines": n["text"].count("\n") + 1,
                    "layout": n.get("copyFit", {}),
                    "font_size": n["fontSize"],
                    "width_px": n["width"],
                    "purpose": (
                        "확인된 강점을 짧은 두 줄 명사구로 표현. 문장형 종결·마침표는 사용하지 않음"
                        if n["id"].startswith("hero.detail-")
                        else TEMPLATE["purposes"].get(
                            n["id"].replace(
                                b["id"] + ".", b.get("templateBlockId", b["id"]) + "."
                            ),
                            "블록 역할에 맞는 짧은 원고",
                        )
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
