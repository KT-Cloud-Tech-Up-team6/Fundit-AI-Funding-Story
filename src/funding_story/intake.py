"""Apply quoted, explicit chat updates to the next generation snapshot."""

import re

from .models import ProjectInput, Review


def apply_changes(source: ProjectInput, review: Review, message: str) -> ProjectInput:
    result = source.model_dump()
    seen = set()
    for change in review.input_changes:
        key = (change.field, change.reward_index)
        if key in seen:
            raise ValueError("같은 입력 필드는 한 번만 수정하세요.")
        seen.add(key)
        if change.quote not in message or change.value not in change.quote:
            raise ValueError("수정 값과 근거 문장은 최신 사용자 메시지의 원문이어야 합니다.")
        if change.field in ("price", "normal_price", "product_count"):
            index = change.reward_index
            if index is None or not 0 <= index < len(result["rewards"]):
                raise ValueError("수정할 등록 선물의 인덱스가 필요합니다.")
            suffix = "개" if change.field == "product_count" else "원"
            # Accept formatting only; do not invent a currency or scale (e.g. 만/천).
            match = re.fullmatch(r"\s*(\d+|\d{1,3}(?:,\d{3})+)\s*" + suffix + r"?\s*", change.value)
            if not match:
                raise ValueError(
                    "숫자는 원문 그대로의 정수와 선택적 원/개만 허용합니다. 단위가 모호하면 변경하지 말고 질문하세요."
                )
            result["rewards"][index][change.field] = int(match[1].replace(",", ""))
        else:
            if change.reward_index is not None:
                raise ValueError("선물 필드가 아니면 reward_index는 null이어야 합니다.")
            if change.field == "tone":
                result["tone"] = change.value
            else:
                result["information"][change.field] = change.value
    return ProjectInput.model_validate(result)


def parse_review(raw, source: ProjectInput, message: str) -> Review:
    review = Review.model_validate(raw)
    apply_changes(source, review, message)
    return review
