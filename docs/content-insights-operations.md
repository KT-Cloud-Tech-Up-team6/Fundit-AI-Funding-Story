# Content Insights 운영·연동 안내

기준: 2026-09-18, API 0.3.0 / policy v2 / Storyline schema v2

설계는 [Content Insights API 설계](content-insights-api-design.md), 구현 상태는 [작업 체크리스트](content-insights-implementation-checklist.md), Backend·FE 경계는 [통합 인터페이스 계약](content-insights-integration-interface.md)을 따른다. 이 API는 Project Service가 호출하는 내부 API이며 브라우저에서 직접 호출하지 않는다. 이 문서의 Project Service·FE 단계는 권장 rollout 순서이며 외부 저장소 구현 완료를 의미하지 않는다.

## 로컬 실행

API와 Funding Story 작성, 필수 페이지 요약, 스토리라인 polling worker를 구분해 실행한다.

```sh
uv run uvicorn funding_story.api:app --host 127.0.0.1 --port 58001
uv run python -m funding_story.worker --lane funding-story
uv run python -m funding_story.worker --lane page-summary
uv run python -m funding_story.worker --lane storyline
```

로컬에서 프로세스 수를 줄여야 할 때만 세 lane을 한 worker가 함께 처리한다.

```sh
uv run python -m funding_story.worker --lane all
```

운영에서는 `page-summary`, `storyline`, `funding-story` lane을 별도 Deployment로 둔다. 한 worker 장애가 다른 작업을 고갈시키지 않게 한다.

권장 시작값은 dev에서 queue별 최소 1 replica다. staging에서 실제 처리시간·메모리·실패율을 측정한 뒤 production replica·concurrency·CPU·메모리를 확정한다. KEDA/HPA/Deployment 같은 배포 정책값은 `Fundit-Infra`가 아니라 ArgoCD가 읽는 `Fundit-GitOps`에서 관리한다. `Fundit-Infra`는 EKS·네트워크·DB와 컨트롤러 설치 상태를 확인하는 근거로 사용한다.

## smoke test

```sh
export AI_BASE_URL=http://127.0.0.1:58001
export AI_INTERNAL_TOKEN=local-integration-only-change-for-deployment
export PROJECT_ID=11111111-1111-1111-1111-111111111111

curl -i -X POST "$AI_BASE_URL/api/v1/ai/content-insight-runs" \
  -H "Authorization: Bearer $AI_INTERNAL_TOKEN" \
  -H "X-Project-Id: $PROJECT_ID" \
  -H 'Content-Type: application/json' \
  --data '{
    "source_revision": 1,
    "idempotency_key": "11111111-1111-1111-1111-111111111111:content-insights:1",
    "trigger": "PROJECT_REGISTRATION_COMPLETED",
    "requested_artifacts": ["PAGE_SUMMARY", "STORYLINE"],
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
curl -s "$AI_BASE_URL/api/v1/ai/content-insight-runs/$RUN_ID" \
  -H "Authorization: Bearer $AI_INTERNAL_TOKEN" \
  -H "X-Project-Id: $PROJECT_ID"
```

등록 readiness 조건은 parent `status`가 아니라 `required_artifacts_ready=true`다. policy v2에서는 `PAGE_SUMMARY`와 `STORYLINE`이 모두 필수이므로 둘 다 성공해야 true다. 프로젝트 원본과 outbox transaction은 AI 결과를 동기적으로 기다리지 않는다.

Storyline 성공 결과는 schema v2여야 한다. AI 내부 응답은 `REWARD_IDENTITY`, `PROJECT_REASON` 순서의 두 section을 가지며 각 section은 headline·description 한 줄을 가진다. Project Service 공개 응답과 FE에서는 내부 role을 제거하고 문구만 표시한다.

일시 오류로 `retryable=true`가 된 한 artifact만 재시도한다.

```sh
curl -i -X POST "$AI_BASE_URL/api/v1/ai/content-insight-runs/$RUN_ID/artifacts/STORYLINE/retry" \
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
- 새 source revision을 생성하면 직전 run과 artifact는 `STALE`이 되어 공개 기준으로 사용할 수 없다.

수동 점검 시 snapshot 원문이나 서비스 token을 로그에 복사하지 않는다. 구조화 로그의 `content_insight_artifact_succeeded|failed`에서 artifact type, run/artifact ID, project ID, source revision, prompt version, 시도 횟수, duration을 확인한다.

## 배포와 롤백

권장 배포 순서는 다음과 같다.

1. DB 복구 지점 확인 후 기존 Flyway migration을 validate한다. 이번 버전은 AI DB schema를 추가하지 않는다.
2. API 0.3.0을 배포하되 Project Service trigger는 아직 켜지 않는다.
3. Page Summary·Storyline polling worker를 배포하고 `/health/ready`를 확인한다.
4. smoke test로 두 artifact의 독립 lane 처리와 상태 조회를 확인한다.
5. Project Service의 Content Insights outbox/trigger를 배포한다.
6. 기존 최신 행 중 `STORYLINE.required!=true`이거나 schema v2 section 두 개가 없는 프로젝트를 새 revision으로 재생성한다. Project Service 공개 mapper는 이 구버전 결과를 readiness 완료로 인정하지 않는다.
7. 두 필수 lane의 대기시간·실패율과 Project Service의 required readiness를 확인한 뒤 등록 readiness를 활성화한다.
8. 마지막으로 FE가 Project Service의 canonical 결과를 조회하도록 전환한다.

롤백할 때는 먼저 Project Service의 신규 trigger/gate를 비활성화한다. 이미 접수된 artifact는 완료하거나 운영 정책에 따라 `STALE` 처리한 뒤 worker를 내린다. API endpoint와 기존 Funding Story 필드는 호환 기간 동안 유지하므로 AI 0.3.0을 먼저 제거할 필요는 없다.

## 권장 관측 기준

첫 배포의 권장 시작 기준은 다음과 같다. 실제 트래픽을 측정한 뒤 변경하며 변경 이유를 체크리스트에 기록한다.

- 작업 대기시간 2분 이상 warning, 5분 이상 critical 후보
- `RUNNING` 10분 초과 작업을 stuck 작업 후보로 탐지
- artifact type별 요청 수, 성공률, 실패율, retry율, p50/p95 duration과 모델 비용 집계
- `required_artifacts_ready=false` 장기 지속 프로젝트 수 모니터링
- snapshot 원문·서비스 token·서명 URL·이미지 바이트는 로그와 trace에서 제외

## 간이 실제 모델 평가

대표 카테고리의 프로젝트 5건 이상으로 배포 전 한 번 평가한다.

- Page Summary: 2~3문장, 입력 사실만 사용, 수치·단위·조건 보존
- Storyline: 정확히 두 블록, 각 headline·description 한 줄, 고정 순서
- Storyline 문구: 내부 역할명과 `DIFFERENCE` 미노출, `~다`·`~습니다`가 아닌 개조식
- 두 artifact 모두 입력에 없는 효능·인증·수치·제작 배경 미생성

전 항목을 만족하면 간이 품질 승인을 통과한 것으로 기록한다. 실패 사례는 prompt fixture로 추가한 뒤 재평가한다.

## 미확정 운영값

아래 값은 코드 기본값으로 확정하지 않는다.

- artifact별 worker replica·concurrency·CPU/메모리
- AI snapshot·결과 보존 기간과 정리 방식
- LangSmith 전송 범위와 보존 기간
- 실제 트래픽 기준 경보 임계값 보정과 당직 알림 채널

이 값이 정해지면 체크리스트 변경 기록과 배포 환경 설정을 함께 갱신한다.
