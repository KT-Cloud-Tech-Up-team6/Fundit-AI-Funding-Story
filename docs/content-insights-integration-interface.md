# 상세 페이지 AI 요약 — AI ↔ BE 인터페이스

상태: AI 구현 기준. BE·FE 구현 및 BE ↔ FE 공개 계약은 각 저장소에서 확인

`PAGE_SUMMARY`는 프로젝트 상세 페이지 상단에 배치할 AI 요약이다. `WHAT`·`WHY`는 AI 출력의 두 역할이며, Funding Story 작성 기능과 독립적으로 실행할 수 있다. 이 문서는 AI의 입력·출력·상태 계약만 다룬다.

## Page Summary

```text
POST /api/v1/ai/page-summary-runs
→ GET /api/v1/ai/page-summary-runs/{run_id} → PAGE_SUMMARY 결과
```

| 메서드 | AI 경로 | 용도 |
|---|---|---|
| `POST` | `/api/v1/ai/page-summary-runs` | 요약 생성 요청 |
| `GET` | `/api/v1/ai/page-summary-runs/{run_id}` | 상태·결과 조회 |
| `POST` | `/api/v1/ai/page-summary-runs/{run_id}/retry` | 재시도 가능한 실패 재시도 |

요청 헤더: `Authorization: Bearer {internal-service-token}`, `X-Project-Id: {project-uuid}`.
요청 본문: `source_revision`, `idempotency_key`, `trigger`, `project_snapshot`.
`trigger`는 최초 등록 `PROJECT_REGISTRATION_COMPLETED`, 콘텐츠 수정 `PROJECT_CONTENT_UPDATED`.

| snapshot 필드 | 입력 |
|---|---|
| `title`, `category`, `description` | 프로젝트의 현재 저장값 |
| `rewards[]` | 리워드 이름·설명·가격(있을 때) |
| `story_content[]` | 원본 순서의 `TEXT`(일반 텍스트 또는 HTML)·`IMAGE`(BE 소유의 안정적인 HTTPS `fileUrl`) 블록 |

`TEXT` HTML의 이미지·영상 태그는 별도 `IMAGE` 블록으로 분리해야 한다. GIF·영상은 이번 입력 계약에서 제외한다. `IMAGE.value`는 안정적인 이미지 주소이며, 서명 URL·바이트를 넣지 않는다. AI는 읽을 수 있는 `value`를 직접 가져오거나, 요청에 선택적으로 포함된 `read_url`·`content_type`·`file_size`·`expires_at`으로 이미지를 가져온다. 읽기 URL이 필요한 경우 네 필드를 함께 전달해야 한다. 이미지 바이트는 결과 레코드에 저장하지 않는다.

```json
{
  "story_content": [
    {"type": "TEXT", "value": "<p>손으로 만든 표지</p>"},
    {"type": "IMAGE", "value": "https://{bucket}.s3.{region}.amazonaws.com/projects/{projectId}/body.png", "read_url": "https://{bucket}.s3.{region}.amazonaws.com/projects/{projectId}/body.png?X-Amz-...", "content_type": "image/png", "file_size": 123, "expires_at": "2099-01-01T00:00:00Z"},
    {"type": "TEXT", "value": "<p>안쪽은 점선 노트</p>"}
  ]
}
```

이미지 접근 방식은 BE 연동 시 확정한다. 별도 BE 이미지 읽기 API는 AI의 선행 조건이 아니다. 이미지 읽기 실패 시 Page Summary는 실패하며 텍스트만으로 성공 처리하지 않는다.
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

AI 응답의 `required_artifacts_ready=true`는 `PAGE_SUMMARY.status=SUCCEEDED`일 때 충족된다. Page Summary 요청은 `STORYLINE`을 실행하지 않는다. 새 `source_revision` 요청 시 이전 AI run은 `STALE`로 관리한다.

## 분리된 Storyline

| 메서드 | AI 경로 | 용도 |
|---|---|---|
| `POST` | `/api/v1/ai/storyline-runs` | 명시적 `STORY_CONFIRMED` 요청 |
| `GET` | `/api/v1/ai/storyline-runs/{run_id}` | 상태·결과 조회 |
| `POST` | `/api/v1/ai/storyline-runs/{run_id}/retry` | 재시도 가능한 실패 재시도 |

Storyline 출력은 `output.content` 단일 문자열이다. Storyline은 텍스트 입력만 사용하며 이미지 전용 snapshot은 허용하지 않는다. Page Summary 요청에서 자동 실행되지 않으며, 후속 소비자는 아직 확정되지 않았다.
