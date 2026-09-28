# Content Insights 운영·연동 안내

기준: policy v3 / Page Summary schema v2

현행 계약은 [AI ↔ BE 인터페이스](content-insights-integration-interface.md)와 [OpenAPI](openapi.json)를 따른다. [초기 설계](content-insights-api-design.md)와 [작업 체크리스트](content-insights-implementation-checklist.md)는 이전 정책 기록이다. 이 문서는 AI 서비스 운영 범위만 다룬다.

## 로컬 실행

API와 Funding Story 작성, 필수 페이지 요약 worker를 구분해 실행한다. Storyline worker는 소비자가 정해진 뒤 별도로 실행한다.

```sh
uv run uvicorn funding_story.api:app --host 127.0.0.1 --port 58001
uv run python -m funding_story.worker --lane funding-story
uv run python -m funding_story.worker --lane page-summary
```

로컬에서 프로세스 수를 줄여야 할 때는 모든 lane을 한 worker가 함께 처리할 수 있다.

```sh
uv run python -m funding_story.worker --lane all
```

운영에서는 `page-summary`와 `funding-story` lane을 별도 Deployment로 둔다. Storyline 소비자가 확정되면 그 lane도 별도 Deployment로 둔다.

권장 시작값은 dev에서 queue별 최소 1 replica다. staging에서 실제 처리시간·메모리·실패율을 측정한 뒤 production replica·concurrency·CPU·메모리를 확정한다. KEDA/HPA/Deployment 같은 배포 정책값은 `Fundit-Infra`가 아니라 ArgoCD가 읽는 `Fundit-GitOps`에서 관리한다. `Fundit-Infra`는 EKS·네트워크·DB와 컨트롤러 설치 상태를 확인하는 근거로 사용한다.

## smoke test

```sh
export AI_BASE_URL=http://127.0.0.1:58001
export AI_INTERNAL_TOKEN=local-integration-only-change-for-deployment
export PROJECT_ID=11111111-1111-1111-1111-111111111111

curl -i -X POST "$AI_BASE_URL/api/v1/ai/page-summary-runs" \
  -H "Authorization: Bearer $AI_INTERNAL_TOKEN" \
  -H "X-Project-Id: $PROJECT_ID" \
  -H 'Content-Type: application/json' \
  --data '{
    "source_revision": 1,
    "idempotency_key": "11111111-1111-1111-1111-111111111111:content-insights:1",
    "trigger": "PROJECT_REGISTRATION_COMPLETED",
    "project_snapshot": {
      "title": "LUMI S1",
      "category": "테크·가전",
      "description": "약 1.3kg 본체를 갖춘 무선 청소기입니다.",
      "rewards": [{"name": "얼리버드", "description": "본체와 틈새 노즐 구성", "price": 129000}],
      "story_content": [{"type": "TEXT", "value": "좁은 공간을 자주 청소하는 사용자를 위해 준비했습니다."}]
    }
  }'
```

응답의 `run_id`로 조회한다.

```sh
curl -s "$AI_BASE_URL/api/v1/ai/page-summary-runs/$RUN_ID" \
  -H "Authorization: Bearer $AI_INTERNAL_TOKEN" \
  -H "X-Project-Id: $PROJECT_ID"
```

AI 응답의 `required_artifacts_ready=true`는 `PAGE_SUMMARY.status=SUCCEEDED`일 때 충족된다. Storyline은 Page Summary 요청에서 실행되지 않는다.

Page Summary 성공 결과는 schema v2의 `sections` 배열(고정 순서의 `WHAT`, `WHY`)이며, 각 블록은 한 줄짜리 `headline`·`description`을 포함한다. 별도 Storyline 결과는 단일 `content`다.

일시 오류로 `retryable=true`가 된 한 artifact만 재시도한다.
이미지 포함 입력은 BE가 접근 가능한 URL 또는 요청에 포함된 읽기 URL을 제공한 뒤 사용한다. 비공개 객체의 읽기 방식과 서명 URL 만료 후 재시도 정책은 BE 연동 이슈에서 확정한다.

```sh
curl -i -X POST "$AI_BASE_URL/api/v1/ai/page-summary-runs/$RUN_ID/retry" \
  -H "Authorization: Bearer $AI_INTERNAL_TOKEN" \
  -H "X-Project-Id: $PROJECT_ID"
```

`retryable=false`, 이미 성공한 결과, 요청되지 않은 결과, `STALE` run은 409다. 이 경우 입력을 보완하고 더 큰 `source_revision`과 새 idempotency key로 요청한다.

## 장애 복구

- API가 작업을 PostgreSQL에 `QUEUED`로 저장하면 polling worker가 회수한다.
- `RUNNING` lease는 30초마다 갱신되고 worker 중단 후 최대 90초 뒤 다시 회수된다.
- PostgreSQL row lock과 lease token이 중복 실행을 막는다.
- 공급자 429/5xx는 한 모델 호출 안에서 15초·30초 간격으로 최대 3회 시도한다. 모두 실패하면 artifact가 `FAILED`, `retryable=true`가 된다.
- 출력 형식 오류는 최초 호출 뒤 최대 2회 재생성한다. 계속 실패하면 `retryable=false`이며 새 revision으로 요청한다.
- 새 source revision을 요청하면 직전 AI run과 artifact는 `STALE`이 된다.

수동 점검 시 snapshot 원문이나 서비스 token을 로그에 복사하지 않는다. 구조화 로그의 `content_insight_artifact_succeeded|failed`에서 artifact type, run/artifact ID, project ID, source revision, prompt version, 시도 횟수, duration을 확인한다.

## 배포와 롤백

AI 서비스 배포 순서는 다음과 같다.

1. DB 복구 지점 확인 후 기존 Flyway migration을 validate한다. 이번 버전은 AI DB schema를 추가하지 않는다.
2. API 0.3.0을 배포한다.
3. Page Summary polling worker를 배포하고 `/health/ready`를 확인한다. Storyline worker는 별도 소비자가 정해질 때 배포한다.
4. smoke test로 Page Summary 처리와 상태 조회를 확인한다.

롤백할 때 이미 접수된 artifact의 처리 상태를 확인한 뒤 worker를 내린다. 외부 호출 중단·재개는 호출 측과 조율한다. 기존 Funding Story 작성 API 필드는 유지하지만, 폐기한 `/content-insight-runs` 경로는 이 버전에 남기지 않는다.

## 권장 관측 기준

첫 배포의 권장 시작 기준은 다음과 같다. 실제 트래픽을 측정한 뒤 변경하며 변경 이유를 체크리스트에 기록한다.

- 작업 대기시간 2분 이상 warning, 5분 이상 critical 후보
- `RUNNING` 10분 초과 작업을 stuck 작업 후보로 탐지
- artifact type별 요청 수, 성공률, 실패율, retry율, p50/p95 duration과 모델 비용 집계
- `required_artifacts_ready=false` 장기 지속 프로젝트 수 모니터링
- snapshot 원문·서비스 token·서명 URL·이미지 바이트는 로그와 trace에서 제외

## 간이 실제 모델 평가

대표 카테고리의 프로젝트 5건 이상으로 배포 전 한 번 평가한다.

- Page Summary: 정확히 두 블록, 각 headline·description 한 줄, 고정 순서, 입력 사실·수치·단위·조건 보존
- Storyline을 별도로 시험할 때: 단일 content 2~3문장, 입력 사실만 사용
- 두 artifact 모두 입력에 없는 효능·인증·수치·제작 배경 미생성

전 항목을 만족하면 간이 품질 승인을 통과한 것으로 기록한다. 실패 사례는 prompt fixture로 추가한 뒤 재평가한다.

## 미확정 운영값

아래 값은 코드 기본값으로 확정하지 않는다.

- artifact별 worker replica·concurrency·CPU/메모리
- AI snapshot·결과 보존 기간과 정리 방식
- LangSmith 전송 범위와 보존 기간
- 실제 트래픽 기준 경보 임계값 보정과 당직 알림 채널

이 값이 정해지면 체크리스트 변경 기록과 배포 환경 설정을 함께 갱신한다.
