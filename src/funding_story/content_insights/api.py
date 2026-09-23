from typing import Annotated

from fastapi import APIRouter, Body

from ..bootstrap import content_insights_application
from ..http_contract import API_PREFIX
from ..observability import emit
from ..security import Project
from .models import ArtifactType, ContentInsightCreateRequest, ContentInsightRunResponse

router = APIRouter(prefix=API_PREFIX, tags=["content-insights"])

CREATE_EXAMPLE = {
    "source_revision": 42,
    "idempotency_key": "11111111-1111-1111-1111-111111111111:content-insights:42",
    "trigger": "PROJECT_REGISTRATION_COMPLETED",
    "requested_artifacts": ["PAGE_SUMMARY", "STORYLINE"],
    "project_snapshot": {
        "title": "LUMI S1",
        "category": "테크·가전",
        "description": "약 1.3kg 본체를 갖춘 무선 청소기입니다.",
        "rewards": [
            {
                "name": "얼리버드",
                "description": "본체와 틈새 노즐 구성",
                "price": 129000,
            }
        ],
        "story_content": [{"type": "TEXT", "value": "좁은 공간의 청소 부담을 줄이기 위해 준비했습니다."}],
    },
}

RUN_EXAMPLE = {
    "run_id": "22222222-2222-2222-2222-222222222222",
    "revision": 1,
    "status": "QUEUED",
    "source_revision": 42,
    "required_artifacts_ready": False,
    "artifacts": {
        "PAGE_SUMMARY": {
            "artifact_id": "33333333-3333-3333-3333-333333333333",
            "status": "QUEUED",
            "required": True,
            "attempts": 0,
            "output": None,
            "prompt_version": None,
            "model": None,
            "error": None,
        },
        "STORYLINE": {
            "artifact_id": "44444444-4444-4444-4444-444444444444",
            "status": "QUEUED",
            "required": True,
            "attempts": 0,
            "output": None,
            "prompt_version": None,
            "model": None,
            "error": None,
        },
    },
}

CreateBody = Annotated[
    ContentInsightCreateRequest,
    Body(openapi_examples={"registration": {"summary": "등록 완료 생성", "value": CREATE_EXAMPLE}}),
]


def kick(artifact_id: str, artifact_type: ArtifactType) -> None:
    emit(
        "content_insight_job_queued",
        artifact_id=artifact_id,
        artifact_type=artifact_type.value,
    )


@router.post(
    "/content-insight-runs",
    status_code=202,
    response_model=ContentInsightRunResponse,
    responses={
        202: {
            "description": "독립 artifact 작업이 접수됨",
            "content": {"application/json": {"example": RUN_EXAMPLE}},
        }
    },
)
def create_content_insight_run(body: CreateBody, project: Project):
    result, dispatch = content_insights_application.create_run(body, project)
    for artifact_id, artifact_type in dispatch:
        kick(artifact_id, artifact_type)
    return result


@router.get("/content-insight-runs/{run_id}", response_model=ContentInsightRunResponse)
def get_content_insight_run(run_id: str, project: Project):
    return content_insights_application.get_run(run_id, project)


@router.post(
    "/content-insight-runs/{run_id}/artifacts/{artifact_type}/retry",
    status_code=202,
    response_model=ContentInsightRunResponse,
)
def retry_content_insight_artifact(run_id: str, artifact_type: ArtifactType, project: Project):
    result, dispatch = content_insights_application.retry_artifact(run_id, artifact_type, project)
    kick(*dispatch)
    return result
