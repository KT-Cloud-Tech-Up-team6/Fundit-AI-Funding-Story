# Content Insights 통합 인터페이스 계약

기준: 2026-09-18, AI API 0.3.0 / policy v2 / Storyline schema v2
상태: 인터페이스 확정. Backend·FE 구현과 배포는 각 저장소의 별도 작업

이 문서는 `Fundit-AI-Funding-Story`와 Project Service·FE 사이의 통합 경계만 정의한다. AI 저장소의 릴리스에 Backend·FE 코드를 포함하거나 해당 저장소의 구현 완료를 전제하지 않는다.

## 책임

| 주체 | 책임 |
|---|---|
| Project Service | 등록 완료 판단, 정규 snapshot과 단조 증가 revision 생성, 내부 인증 호출, 최신 성공 결과 영구 저장 |
| Funding Story AI | parent run 접수, Page Summary·Storyline 독립 실행/상태/재시도, 이전 revision stale 처리 |
| FE | Project Service 공개 API만 호출, 성공한 최신 결과 표시, 내부 AI role 미노출 |

FE는 AI 내부 토큰을 보유하거나 AI API를 직접 호출하지 않는다.

## 생성 흐름

```text
프로젝트 기본정보·스토리·리워드 저장 완료
  -> Project Service가 source_revision과 불변 snapshot 확정
  -> POST /v1/content-insight-runs
  -> GET /v1/content-insight-runs/{run_id} polling
  -> 두 required artifact 성공 확인
  -> Project Service가 최신 revision 결과 저장
  -> FE가 Project Service 공개 응답 조회
```

Project Service의 원본 저장 transaction은 AI 완료를 동기 대기하지 않는다. 운영 구현에서는 같은 transaction의 durable outbox 또는 동등한 전달 보장 방식을 권장한다.

## Project Service → AI

모든 요청에 다음 헤더가 필요하다.

```http
Authorization: Bearer {internal-service-token}
X-Project-Id: {project-uuid}
```

생성 endpoint:

```http
POST /v1/content-insight-runs
```

핵심 요청 필드:

- `source_revision`: 프로젝트별 최신 여부를 판정할 양의 정수
- `idempotency_key`: 동일 revision의 중복 접수를 막는 키
- `trigger`: 최초 등록은 `PROJECT_REGISTRATION_COMPLETED`, 이후 내용 변경은 `PROJECT_CONTENT_UPDATED`
- `requested_artifacts`: 등록·수정 trigger에서는 누락하더라도 policy v2가 `PAGE_SUMMARY`, `STORYLINE`을 모두 요청하고 필수로 판정
- `project_snapshot`: 제목·카테고리·설명·리워드·TEXT 스토리 블록만 포함

이미지·동영상 URL, 파일 바이트, 사용자 인증 정보는 snapshot에 넣지 않는다. 전체 schema와 오류 응답은 [OpenAPI](openapi.json)를 기준으로 한다.

상태 조회와 단일 artifact 재시도:

```http
GET  /v1/content-insight-runs/{run_id}
POST /v1/content-insight-runs/{run_id}/artifacts/{PAGE_SUMMARY|STORYLINE}/retry
```

Project Service는 parent `status`가 아니라 `required_artifacts_ready`와 각 artifact 상태를 함께 저장한다. 더 최신 revision이 존재하면 이전 성공 결과를 공개 기준으로 사용하지 않는다.

## Project Service → FE 공개 계약

권장 공개 응답은 AI 내부 저장 구조를 그대로 노출하지 않고 다음 필드만 전달한다.

```json
{
  "sourceRevision": 17,
  "status": "SUCCEEDED",
  "requiredArtifactsReady": true,
  "artifacts": [
    {
      "type": "PAGE_SUMMARY",
      "status": "SUCCEEDED",
      "required": true,
      "content": "프로젝트 상세 페이지 요약",
      "sections": []
    },
    {
      "type": "STORYLINE",
      "status": "SUCCEEDED",
      "required": true,
      "content": null,
      "sections": [
        {
          "headline": "핵심 리워드를 압축한 헤드라인",
          "description": "대상과 구성을 구체화한 상세 설명"
        },
        {
          "headline": "프로젝트 필요성을 압축한 헤드라인",
          "description": "문제와 기대 변화를 구체화한 상세 설명"
        }
      ]
    }
  ]
}
```

AI 내부 Storyline section의 `role`은 공개 응답에서 제거한다. `WHAT`, `WHY`, `DIFFERENCE` 같은 내부 의미 구분명도 표시 문구나 공개 필드로 사용하지 않는다.

## FE 표시 규칙

- `SUCCEEDED`인 최신 artifact만 표시
- `PAGE_SUMMARY`는 `페이지 요약`과 `content` 표시
- `STORYLINE`은 `스토리라인` 아래에 순서가 보존된 두 개의 `headline`·`description` 블록 표시
- `PENDING`, `FAILED`, `NOT_REQUESTED`, `STALE`은 첫 버전의 공개 화면에서 생략
- Live Summary는 Content Insights artifact가 아니며 별도 결과가 실제로 있을 때만 `라이브 요약`으로 조합
- 사용자 입력 확인 요약과 기존 Funding Story export의 `project_summary`는 공개 canonical 결과로 사용하지 않음

## 외부 저장소 적용 체크

Backend·FE 구현 작업에서는 다음을 별도로 검증한다.

- 프로젝트 원본 저장과 outbox 기록의 원자성
- 중복 전달·polling 재시작·이전 revision 지연 응답 처리
- policy v1 결과가 policy v2 readiness를 충족하지 않도록 하는 호환 처리
- 공개 응답에서 Storyline 내부 role 제거
- FE가 AI API를 우회 호출하지 않는지 확인
- 실제 배포 환경에서 Project Service → AI HTTP smoke test

Backend·FE 코드와 해당 저장소 테스트는 이 문서의 완료 범위에 포함하지 않는다.
