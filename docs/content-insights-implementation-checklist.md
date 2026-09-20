# Content Insights 구현 체크리스트

최초 작성: 2026-09-17
최종 검증: 2026-09-18
상태: AI API 0.3.0과 제품 정책 구현 완료. Backend·FE는 통합 인터페이스만 확정했으며 실제 구현·배포는 외부 저장소 작업.

설계 문서: [필수 프로젝트 콘텐츠 생성 API 설계](content-insights-api-design.md)
통합 계약: [Content Insights 통합 인터페이스](content-insights-integration-interface.md)

> **유동적 체크리스트 운영 원칙**
> 이 체크리스트는 고정된 계약이나 완료 순서가 아니다. 구현·연동·검증 과정에서 새 제약, 실패 사례, 제품 결정 또는 운영 요구가 발견되면 항목을 추가·삭제·분할·통합하거나 우선순위를 변경할 수 있다. 변경할 때는 완료된 작업을 근거 없이 지우지 않고, 변경 이유와 영향을 아래 변경 기록에 남긴다. 각 구현 단계와 PR이 끝날 때 현재 코드와 체크리스트가 일치하는지 다시 확인한다.

상태 표기:

- `[ ]`: 시작 전 또는 미완료
- `[x]`: 검증 근거까지 확인한 완료
- `보류`: 외부 결정이나 선행 작업이 필요한 항목
- `제외`: 범위에서 제거했으며 변경 기록에 이유가 남은 항목

완료 판단은 코드가 존재하는 것만으로 하지 않는다. 자동 테스트, 빌드 또는 계약 문서 중 해당 항목에 맞는 근거까지 확인해야 `[x]`로 표시한다. 제품 정책을 임의로 확정해야 하는 항목은 기능 경계를 구현했더라도 `보류`로 유지한다.

Backend·FE 항목의 `[x]`는 인터페이스 결정과 문서화 완료를 뜻한다. 외부 저장소 코드 구현은 별도 항목으로 표시하며 이 저장소의 릴리스 완료 근거에 포함하지 않는다.

## 0. 확정된 방향

- [x] `Fundit-AI-Funding-Story` 저장소를 유지한다.
- [x] 선택적 Funding Story 작성과 필수 Content Insights 생성을 분리한다.
- [x] 외부 생성 진입점은 `POST /api/v1/ai/content-insight-runs` 하나로 설계한다.
- [x] `PAGE_SUMMARY`와 `STORYLINE`을 독립 artifact로 모델링한다.
- [x] `PAGE_SUMMARY`는 프로젝트 등록 완료 시 필수 생성한다.
- [x] Project Service의 snapshot과 revision을 입력 기준으로 사용하는 계약을 확정한다.
- [x] `STORYLINE`은 `PROJECT_REGISTRATION_COMPLETED`에서 최초 실행한다.
- [x] `STORYLINE`을 Page Summary와 함께 required로 판정한다.
- [ ] 기본정보·스토리·리워드가 준비된 등록 완료 조건에서 outbox를 생성한다. `외부 Backend 저장소 적용`

## 1. API·도메인 계약

- [x] `ArtifactType`에 `PAGE_SUMMARY`, `STORYLINE`을 정의한다.
- [x] trigger enum의 첫 범위를 정의한다.
  - [x] `PROJECT_REGISTRATION_COMPLETED`
  - [x] `PROJECT_CONTENT_UPDATED`
  - [x] 향후 분리 경계용 `STORY_CONFIRMED`
- [x] `ProjectSnapshot` v1 필드를 BE mapper와 일치시킨다.
  - [x] 제목·카테고리·설명
  - [x] 리워드 이름·설명·가격
  - [x] 상세 콘텐츠는 `TEXT` 블록으로 정규화
  - [x] 빈 값과 길이 제한
  - [x] 이미지·동영상 URL·첨부 입력 제외
- [x] 생성 요청 model을 정의한다.
  - [x] `source_revision`
  - [x] `idempotency_key`
  - [x] `trigger`
  - [x] `requested_artifacts`
  - [x] `project_snapshot`
- [x] parent run 응답 model을 정의한다.
- [x] artifact 응답 model을 정의한다.
- [x] artifact별 output schema version을 정의한다.
- [x] parent와 artifact 상태 enum을 정의한다.
- [x] `required_artifacts_ready` 계산 규칙을 독립 함수와 상태 테스트로 검증한다.
- [x] 오류 code와 HTTP mapping을 정의한다.
- [x] 요청·응답 예시를 OpenAPI에 추가하고 저장된 fixture 일치를 검사한다.

## 2. Generation Policy

- [x] trigger별 requested/required artifact를 반환하는 policy port를 정의한다.
- [x] `PAGE_SUMMARY`를 등록 완료 trigger에서 `requested=true`, `required=true`로 설정한다.
- [x] 등록·수정 시 `STORYLINE`을 항상 요청하고 필수 결과로 설정한다.
- [x] 호출자가 등록 완료 요청에서 Page Summary를 누락해도 서버 policy가 필수 artifact를 추가한다.
- [x] 지원하지 않는 trigger/artifact 조합을 거부한다.
- [x] policy 결과를 parent run에 저장해 이후 설정 변경과 기존 run을 구분한다.
- [x] `content-insights-policy-v2`를 기록한다.
- [x] 스토리라인 trigger 변경이 Page Summary 계약에 영향을 주지 않는 회귀 테스트를 추가한다.

## 3. 내부 모듈 뼈대

- [x] `src/funding_story/content_insights/` feature package를 추가한다.
- [x] FastAPI router를 기존 `funding_story.api` composition에 연결한다.
- [x] API 계층이 application service만 호출하도록 구성한다.
- [x] content insight application service를 추가한다.
- [x] artifact generator protocol을 추가한다.
- [x] generator registry를 추가한다.
- [x] Page Summary 전용 input mapper를 추가한다.
- [x] Storyline 전용 input mapper를 추가한다.
- [x] 두 generator가 별도 mapper·prompt로 동작하는 구조 테스트를 추가한다.
- [x] 공통 provider retry·출력 검증·구조화 로그를 재사용한다.

## 4. 저장·멱등성

- [x] 기존 `ai_records`/`ai_requests` 재사용안을 검토하고 첫 릴리스에 채택한다.
- [x] `content_insight_run` parent record schema를 정의한다.
- [x] `content_insight_artifact` child record schema를 정의한다.
- [x] parent에 artifact ID와 policy 결과를 저장한다.
- [x] artifact에 source revision/hash, prompt/model/schema version을 저장한다.
- [x] parent idempotency fingerprint 계산에 snapshot·trigger·requested artifact를 포함한다.
- [x] 같은 키·같은 payload 재호출이 기존 run을 반환하는 테스트를 추가한다.
- [x] 같은 키·다른 payload가 `409`를 반환하는 테스트를 추가한다.
- [x] artifact별 작업 key와 advisory lock 규칙을 추가한다.
- [x] AI가 새 source revision 접수 시 이전 run을 `STALE`로 구분한다.
- [ ] AI 저장소의 필수 결과 보존 기간을 확정한다. `보류: 인프라/보안/BE`
- [x] AI가 generic record를 재사용하는 것으로 확정한다.
- [ ] Project Service에 전용 `project_content_insights` table과 forward-only migration을 추가한다. `외부 Backend 저장소 적용`

## 5. 비동기 실행·큐

- [x] Content Insights parent run이 artifact child job을 생성하도록 구현한다.
- [x] 기존 `funding.execute`의 분기를 명시적 chat/run 처리로 제한하고 Content Insights는 전용 task로 분리한다.
- [x] `content-insights.page-summary` task/queue를 추가한다.
- [x] `content-insights.storyline` task/queue를 추가한다.
- [x] 선택적 authoring/image queue와 subscription을 분리한다.
- [ ] Page Summary worker의 concurrency와 resource limit 기본값을 정한다. `보류: 운영 부하 측정`
- [x] broker 전달 실패 시 DB에 남은 artifact를 dispatcher가 재전달하는 경로를 테스트한다.
- [x] 중복 delivery에서 generator가 두 번 실행되지 않도록 lock 테스트를 추가한다.
- [ ] 공급자 retry를 artifact별 설정으로 분리한다. `보류: 현재 공통 429/5xx 3회, 15초·30초 backoff로 첫 릴리스 운영`
- [x] 출력 계약 오류를 최초 호출 포함 최대 3회로 제한한다.
- [x] 성공한 artifact를 parent aggregate 상태에 반영한다.
- [x] 한 artifact 실패·재시도가 다른 artifact 상태를 덮어쓰지 않는 테스트를 추가한다.

## 6. Page Summary Generator

- [x] 공개 상세 상단에서 서포터에게 표시하는 2~3문장·최대 600자 요약으로 확정한다.
- [x] Page Summary input mapper의 포함·제외 필드를 정의한다.
- [x] `page-summary-v1` prompt를 작성한다.
- [x] prompt에서 입력에 없는 사실·인증·수치 생성을 금지한다.
- [x] 수치·단위·조건 보존 규칙을 적용한다.
- [x] 상세 콘텐츠가 부족할 때 `422` validation error를 정의하고 테스트한다.
- [x] 출력 길이·빈 문자열 validator를 추가한다. 금지 표현은 prompt 제약으로 적용한다.
- [x] output의 `source_fields` 계산 규칙을 정의한다.
- [x] 대표 정상·수치·리워드 fixture를 작성한다.
- [x] 인증·배송·환불 정책 경계 fixture를 보강한다.
- [x] 불충분 입력 fixture를 작성한다.
- [x] prompt injection 형태의 프로젝트 본문 fixture를 작성한다.
- [x] mock 모델 계약 테스트를 추가한다.
- [x] 대표 프로젝트 5건의 사실 보존·가독성 간이 평가 기준을 정의한다. `실제 실행은 운영 준비`

## 7. Storyline Generator

- [x] 공개 상세에 표시할 두 요약 블록과 각 헤드라인·상세 설명 한 줄을 출력 정의로 확정한다.
- [x] 최초 생성 trigger를 프로젝트 등록 완료로 확정한다.
- [x] 기본정보·스토리·리워드 변경과 Funding Story 적용 시 새 revision을 생성하는 trigger 계약을 정의한다.
- [x] Storyline input mapper의 포함·제외 필드를 정의한다.
- [x] `storyline-v2` prompt를 Page Summary prompt와 독립적으로 작성한다.
- [x] 고정 순서의 두 section과 각 headline·description 한 줄을 강제하는 schema v2 validator를 추가한다.
- [x] 내부 역할명과 `DIFFERENCE`가 노출 문구에 포함되지 않도록 검증한다.
- [x] `~다`, `~습니다` 종결형이 아닌 개조식 문체를 검증한다.
- [x] 제작 배경이 없으면 확인된 제품·프로젝트 정보만 연결하도록 규칙을 둔다.
- [x] 입력 사실을 벗어난 감정·창업 배경·고객 반응 생성을 금지한다.
- [x] 정상·수정 revision fixture와 공통 불충분 입력 검증을 추가한다.
- [x] mock 모델 계약 테스트를 추가한다.
- [x] 대표 프로젝트 5건의 구조·문체·사실 보존 간이 평가 기준을 정의한다. `실제 실행은 운영 준비`

## 8. API endpoint

- [x] `POST /api/v1/ai/content-insight-runs`를 구현한다.
- [x] 기존 Bearer token과 `X-Project-Id` 인증을 적용한다.
- [x] 요청 snapshot을 불변 입력으로 저장한 뒤 `202`를 반환한다.
- [x] `GET /api/v1/ai/content-insight-runs/{run_id}`를 구현한다.
- [x] artifact별 output/error를 안정적인 응답 schema로 반환한다.
- [x] `POST /api/v1/ai/content-insight-runs/{run_id}/artifacts/{artifact_type}/retry`를 구현한다.
- [x] 성공 artifact·최신이 아닌 revision·재시도 불가 오류의 `409` 규칙을 적용한다.
- [x] 프로젝트 범위를 벗어난 run 조회·재시도를 거부하는 테스트를 추가한다.
- [x] API OpenAPI JSON을 코드에서 재생성한다.
- [x] 저장된 `docs/openapi.json`과 코드가 일치하는지 검사한다.

## 9. 기존 Funding Story 계약 전환

- [x] `CopyResult.summary`와 `CopyResult.storyline`의 현재 세 저장소 소비자를 목록화한다.
- [x] `ExportResult.project_summary`의 AI·FE·BE 소비자를 목록화한다.
- [x] 기존 필드를 최소 한 릴리스 동안 preview로 유지한다.
- [x] 기존 필드를 `authoring_preview` 의미로 문서화한다.
- [x] canonical 결과가 Content Insights뿐임을 API·통합 계약에 명시한다.
- [ ] Project Service가 새 결과를 저장하는 동안 기존 필드를 호환 유지한다. `외부 Backend 저장소 적용`
- [ ] 모든 소비자 전환 후 기존 copy prompt에서 필수 summary/storyline 생성을 제거한다. `보류: 호환 기간 종료`
- [x] 기존 export JSON fixture와 frontend contract test를 유지·갱신한다.
- [x] 기존 Funding Story session → run → export → commit 회귀 테스트를 유지한다.

## 10. Project Service 통합 인터페이스

- [x] 기본정보·스토리·리워드 준비 시 등록 완료 trigger를 생성하는 조건을 문서화한다.
- [x] canonical snapshot의 필드와 제외 대상을 정의한다.
- [x] 단조 증가 `source_revision`과 idempotency key 계약을 정의한다.
- [x] 프로젝트 원본 저장 후 비동기 전달하는 transaction/outbox 권장 순서를 정의한다.
- [x] AI run ID·source revision·artifact 결과를 보존하는 저장 요구사항을 정의한다.
- [x] polling·재시도·최신 revision 판정 규칙을 정의한다.
- [x] 두 required artifact가 모두 성공해야 readiness가 true라는 계약을 확정한다.
- [ ] Project Service snapshot mapper와 durable outbox를 구현한다. `외부 Backend 저장소 적용`
- [ ] AI 호출·polling worker와 최신 revision 저장을 구현한다. `외부 Backend 저장소 적용`
- [ ] 오래된 응답 차단과 policy v1 호환 처리를 구현한다. `외부 Backend 저장소 적용`
- [ ] Backend 단위·DB migration·통합 테스트를 추가한다. `외부 Backend 저장소 적용`

## 11. FE·공개 조회 계약

이 절의 완료 표시는 표시 규칙과 공개 응답 계약이 확정됐다는 의미이며 FE 코드 적용을 의미하지 않는다.

- [x] 공개 상세 v1 응답을 `artifacts[].type/status/required/content/sections`로 정의한다.
- [x] Page Summary·Storyline·Live Summary를 구분하는 명시적 type을 사용한다.
- [x] FE 표시 label을 `페이지 요약`, `스토리라인`, `라이브 요약`으로 구분한다.
- [x] Storyline의 내부 role을 공개 응답에서 제거하고 두 headline·description 블록만 렌더링하도록 정의한다.
- [x] v1에서는 `SUCCEEDED` 결과만 표시하고 `PENDING`, `FAILED`, `NOT_REQUESTED`, `STALE`은 공개 화면에서 생략한다. `별도 오류 UI는 보류`
- [x] 라이브 미진행 또는 값이 없을 때 Live Summary를 생략한다.
- [x] 사용자 입력 확인 요약이 공개 상세 데이터에 포함되지 않음을 확인한다.
- [x] FE가 AI API를 직접 호출하지 않고 Project Service BFF만 호출하도록 한다.
- [ ] Project Service 공개 mapper와 FE 조회·렌더링을 구현한다. `외부 Backend·FE 저장소 적용`

## 12. 테스트

- [x] domain 상태 전이 단위 테스트를 추가한다.
- [x] generation policy 단위 테스트를 추가한다.
- [x] parent aggregate 상태 테스트를 추가한다.
- [x] `required_artifacts_ready` 조합 테스트를 추가한다.
- [x] artifact별 독립 성공·실패·재시도 테스트를 추가한다.
- [x] revision stale 처리 테스트를 추가한다.
- [x] idempotency 충돌 테스트를 추가한다.
- [x] 프로젝트 자산·기록 격리 테스트를 추가한다.
- [x] broker 장애와 outbox 복구 테스트를 추가한다.
- [x] AI API integration test를 추가한다.
- [ ] Project Service의 snapshot/outbox/polling contract test와 DB migration 통합 테스트를 추가한다. `외부 Backend 저장소 적용`
- [ ] 배포된 AI와 Project Service 사이의 실제 HTTP end-to-end smoke test를 수행한다. `보류: 배포 환경`
- [ ] 기존 policy v1 최신 행을 새 revision으로 backfill한다. `보류: 배포 데이터`
- [x] 실제 모델 호출을 제외한 전체 `pytest` 77개를 통과한다.
- [x] Ruff와 wheel/sdist package build를 통과한다.
- [ ] Backend 저장소 테스트를 통과한다. `외부 Backend 저장소 적용`
- [ ] FE 저장소 테스트·typecheck·lint·production build를 통과한다. `외부 FE 저장소 적용`
- [ ] 실제 모델 샘플에서 제품 승인 기준을 충족한다. `보류: 승인 기준·실제 모델 환경`

## 13. 관측·운영·보안

- [ ] artifact type별 latency·성공률·retry율 metric을 추가한다. `보류: 모니터링 backend 선정`
- [x] queue delay 2분 warning·5분 critical 후보를 첫 배포 권장값으로 정한다. `실트래픽 후 보정`
- [x] `RUNNING` 10분 초과를 stuck 작업 후보로 정한다. `실트래픽 후 보정`
- [x] 성공·실패 구조화 로그에 run/artifact/project/revision/prompt version, attempt, duration을 남긴다.
- [x] snapshot 원문·서비스 token·이미지 바이트가 신규 로그 필드에 남지 않도록 구성한다.
- [ ] artifact별 모델 비용을 집계한다. `보류: 비용 metric backend`
- [ ] API/worker/beat의 DB connection budget을 재계산한다. `보류: replica·concurrency 확정`
- [x] Page Summary worker를 이미지·Storyline worker와 별도 rollout할 수 있도록 독립 queue와 환경변수를 추가한다. `replica/resource 값은 보류`
- [x] 운영 retry와 수동 재처리 절차를 작성한다.
- [ ] LangSmith 전송 범위와 보존 정책을 보안팀과 확인한다. `보류: 보안 정책`

## 14. 문서·릴리스

- [x] Content Insights API 설계 문서를 작성한다.
- [x] 유동적으로 수정 가능한 구현 체크리스트를 작성한다.
- [x] `docs/architecture.md`에 Content Insights 책임과 상태 흐름을 반영한다.
- [x] `docs/development.md`에 worker queue별 실행법을 추가한다.
- [x] `docs/frontend-call-contract.md`에 canonical 결과와 조회 흐름을 반영한다.
- [x] `docs/validation.md`에 자동 검증과 아직 수행하지 않은 실제 모델 검증을 구분해 반영한다.
- [x] `README.md` 기능·아키텍처·문서 링크를 갱신한다.
- [x] BE 연동 예제와 curl smoke test를 작성한다.
- [x] migration/worker/API/BE/FE rollout과 롤백 순서를 작성한다.
- [x] 최소 한 릴리스의 호환 기간과 소비자 전환 확인 후 deprecation·제거를 진행한다.

## 15. AI 저장소 첫 릴리스 완료 조건

- [x] Funding Story 세션 없이 Page Summary 생성 요청이 성공한다.
- [x] 한 요청이 두 artifact 작업을 독립적으로 생성한다.
- [x] Page Summary가 등록 완료 trigger에서 필수 결과로 판정된다.
- [x] Storyline도 등록 완료 trigger에서 필수 결과로 판정된다.
- [x] Storyline schema v2의 두 블록·두 줄 계약과 내부 role 미노출이 검증된다.
- [x] 스토리라인 정책 변경이 Page Summary 계약에 영향을 주지 않는다.
- [x] 한 artifact만 선택해 재시도할 수 있다.
- [x] 같은 요청의 중복 실행과 다른 payload 충돌이 구분된다.
- [x] AI가 더 최신 source revision 접수 시 이전 run을 stale 처리한다.
- [ ] Project Service가 최신 revision 결과만 공개 기준으로 반영한다. `외부 Backend 저장소 적용`
- [x] Page Summary가 이미지 생성과 다른 queue/subscription을 사용한다.
- [x] 기존 선택적 Funding Story 흐름의 회귀 테스트가 통과한다.
- [x] 운영 관측·재처리·롤백 절차가 문서화된다.

## 16. 변경 기록

체크리스트 항목이나 우선순위를 수정할 때 아래 표에 날짜, 변경 내용, 이유, 영향을 기록한다.

| 날짜 | 변경 | 이유 | 영향 |
|---|---|---|---|
| 2026-09-17 | 최초 체크리스트 작성 | 통합 API와 독립 artifact 구조 구현 준비 | 전체 구현·연동·검증 범위 정의 |
| 2026-09-18 | AI API 0.3.0 구현과 외부 통합 인터페이스 검증 결과 반영 | 코드와 체크리스트 상태 동기화 | AI 릴리스와 외부 저장소 적용 범위 구분 |
| 2026-09-18 | Storyline 임시 policy와 향후 `STORY_CONFIRMED` 경계 기록 | 최초 실행 시점이 미확정임 | API를 다시 합치지 않고 이후 별도 endpoint/trigger로 분리 가능 |
| 2026-09-18 | stale parent의 과거 artifact 본문을 공개하지 않는 인터페이스 규칙 확정 | 이전 revision 결과 노출 방지 | 외부 Backend·FE 구현 요구사항으로 전달 |
| 2026-09-18 | Storyline을 등록 시 필수로 확정하고 policy v2 적용 | 제품 정책 확정 | 두 artifact가 모두 성공해야 required readiness 충족 |
| 2026-09-18 | Storyline을 두 section의 headline·description 구조로 변경 | WHAT/WHY 의미는 유지하되 사용자에게 직접 노출하지 않음 | AI schema/prompt v2와 공개 응답·표시 계약 갱신 |
| 2026-09-18 | preview 최소 한 릴리스 유지와 Live Summary 별도 type 확정 | 안전한 소비자 전환 | 운영 전환 후 deprecation 가능 |
| 2026-09-18 | policy v1 결과를 policy v2 readiness로 인정하지 않는 호환 규칙 확정 | 구버전 optional Storyline 오판 방지 | 외부 Backend 적용과 배포 시 기존 프로젝트 backfill 필요 |
| 2026-09-18 | Backend·FE 변경은 push 범위에서 제외하고 통합 인터페이스로만 유지 | 저장소별 책임과 배포 범위 분리 | 외부 구현 항목을 미완료로 재분류하고 통합 계약 문서 추가 |
