import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from pydantic import TypeAdapter, ValidationError

from .config import settings
from .models import PublicId

PROJECT_ID = TypeAdapter(PublicId)


def authorize(
    authorization: Annotated[str | None, Header()] = None,
    x_project_id: Annotated[str | None, Header()] = None,
):
    if not authorization or not secrets.compare_digest(
        authorization, "Bearer " + settings().ai_service_token
    ):
        raise HTTPException(401, "내부 서비스 인증이 필요합니다.")
    try:
        return PROJECT_ID.validate_python(x_project_id)
    except ValidationError as exc:
        raise HTTPException(400, "프로젝트 범위 UUID가 올바르지 않습니다.") from exc


Project = Annotated[str, Depends(authorize)]
