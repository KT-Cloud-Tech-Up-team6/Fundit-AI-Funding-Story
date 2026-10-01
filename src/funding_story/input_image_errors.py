"""Public input-image errors contain fixed text, never storage URLs or decoder errors."""

from typing import Literal

ImageErrorCode = Literal[
    "INPUT_IMAGE_TYPE_MISMATCH", "INPUT_IMAGE_UNSUPPORTED_TYPE", "INPUT_IMAGE_INVALID"
]

_MESSAGES = {
    "INPUT_IMAGE_TYPE_MISMATCH": (
        "이미지의 실제 형식과 업로드된 형식 정보가 다릅니다. 이미지 파일을 확인해 주세요."
    ),
    "INPUT_IMAGE_UNSUPPORTED_TYPE": (
        "지원하지 않는 이미지 형식입니다. JPEG·PNG·WebP 파일을 사용해 주세요."
    ),
    "INPUT_IMAGE_INVALID": (
        "입력 이미지 파일이 손상되었거나 올바른 이미지가 아닙니다. 이미지 파일을 확인해 주세요."
    ),
}


class InputImageValidationError(ValueError):
    def __init__(self, code: ImageErrorCode):
        self.code = code
        super().__init__(_MESSAGES[code])

    def public_error(self) -> dict:
        return {"code": self.code, "message": str(self), "retryable": False, "detail": None}
