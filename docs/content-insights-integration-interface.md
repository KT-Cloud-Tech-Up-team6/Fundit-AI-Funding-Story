# Content Insights 통합 인터페이스 계약

상태: AI 구현 기준. BE·FE 적용 상태는 각 저장소에서 확인

| 주체 | 책임 |
|---|---|
| Project Service | 등록 완료 판단, 불변 snapshot·revision 생성, AI 호출, 최신 성공 결과 저장·공개 |
| Funding Story AI | Page Summary 생성·상태·재시도; Storyline은 별도 API로만 생성 |
| FE | BE 공개 API의 Page Summary 표시; AI 직접 호출 없음 |

## 등록 후 Page Summary

```text
프로젝트 정보 저장 → BE snapshot/revision 확정 → POST /api/v1/ai/page-summary-runs
→ GET /api/v1/ai/page-summary-runs/{run_id} → PAGE_SUMMARY 성공
→ BE 최신 결과 저장 → FE 공개 상세 표시
```

| 메서드 | AI 경로 | 용도 |
|---|---|---|
| `POST` | `/api/v1/ai/page-summary-runs` | 등록·수정 시 요약 생성 |
| `GET` | `/api/v1/ai/page-summary-runs/{run_id}` | 상태·결과 조회 |
| `POST` | `/api/v1/ai/page-summary-runs/{run_id}/retry` | 재시도 가능한 실패 재시도 |

요청 헤더: `Authorization: Bearer {internal-service-token}`, `X-Project-Id: {project-uuid}`.
요청 본문: `source_revision`, `idempotency_key`, `trigger`, `project_snapshot`.
`trigger`는 최초 등록 `PROJECT_REGISTRATION_COMPLETED`, 콘텐츠 수정 `PROJECT_CONTENT_UPDATED`.
snapshot에는 제목·카테고리·설명·리워드·TEXT 스토리 블록만 포함한다. 이미지 바이트·URL과 사용자 인증 정보는 제외한다.
전체 필드와 오류 응답: [OpenAPI](openapi.json).

AI 성공 출력 `artifacts.PAGE_SUMMARY.output`:

| 내부 role | 의미 | headline | description |
|---|---|---|---|
| `WHAT` | 제공하는 핵심 리워드와 프로젝트 정체성 | 핵심 리워드·정체성을 압축 | 실제 구성과 특징으로 구체화 |
| `WHY` | 프로젝트의 필요성·해결 문제·변화 | 필요성이나 문제를 압축 | 입력에 근거한 사용 맥락·변화를 구체화 |

```json
{
  "schema_version": 2,
  "sections": [
    {"role": "WHAT", "headline": "좁은 공간을 위한 무선 청소기", "description": "약 1.3kg 본체와 틈새 노즐로 구성된 얼리버드 리워드"},
    {"role": "WHY", "headline": "좁은 공간의 청소 부담 완화", "description": "좁은 공간을 자주 청소하는 사용자를 위해 준비한 프로젝트"}
  ],
  "source_fields": ["title", "description", "rewards", "story_content"]
}
```

등록 준비 조건은 `required_artifacts_ready=true`와 `PAGE_SUMMARY.status=SUCCEEDED`다. `STORYLINE`은 등록 시 요청하지 않으며 준비 조건에 포함하지 않는다. 이전 revision 결과는 공개 기준으로 사용하지 않는다. 프로젝트 원본 저장은 AI 완료를 동기 대기하지 않는다.

## BE → FE 공개 결과

BE·FE 적용 시 BE는 AI 내부 `role`을 제외하고 최신 `PAGE_SUMMARY`의 두 항목을 전달한다. 아래는 목표 공개 형식이며 BE·FE 코드에는 아직 반영하지 않았다.

```json
{
  "sourceRevision": 17,
  "status": "SUCCEEDED",
  "requiredArtifactsReady": true,
  "artifacts": [{
    "type": "PAGE_SUMMARY",
    "status": "SUCCEEDED",
    "required": true,
    "sections": [
      {"headline": "좁은 공간을 위한 무선 청소기", "description": "약 1.3kg 본체와 틈새 노즐로 구성된 얼리버드 리워드"},
      {"headline": "좁은 공간의 청소 부담 완화", "description": "좁은 공간을 자주 청소하는 사용자를 위해 준비한 프로젝트"}
    ]
  }]
}
```

FE는 성공한 최신 What·Why 두 항목만 표시한다. `PENDING`, `FAILED`, `STALE`은 공개 화면에서 생략한다. Live Summary와 채팅 입력 확인 요약은 별개다.

## 분리된 Storyline

| 메서드 | AI 경로 | 용도 |
|---|---|---|
| `POST` | `/api/v1/ai/storyline-runs` | 명시적 `STORY_CONFIRMED` 요청 |
| `GET` | `/api/v1/ai/storyline-runs/{run_id}` | 상태·결과 조회 |
| `POST` | `/api/v1/ai/storyline-runs/{run_id}/retry` | 재시도 가능한 실패 재시도 |

Storyline 출력은 `output.content` 단일 문자열이다. 후속 AI 큐시트 연동 시점은 미확정이며 등록 과정의 자동 호출·BE 저장·FE 공개 상세 표시 계약에 포함하지 않는다.
