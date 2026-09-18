import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException

from .config import settings


def authorize(
    authorization: Annotated[str | None, Header()] = None,
    x_project_id: Annotated[str | None, Header()] = None,
):
    if not authorization or not secrets.compare_digest(
        authorization, "Bearer " + settings().ai_service_token
    ):
        raise HTTPException(401, "내부 서비스 인증이 필요합니다.")
    if not x_project_id:
        raise HTTPException(400, "프로젝트 범위가 필요합니다.")
    return x_project_id


Project = Annotated[str, Depends(authorize)]
