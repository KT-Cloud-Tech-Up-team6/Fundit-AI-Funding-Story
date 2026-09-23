# 검증 범위

기준: 2026-09-23, Funding Story API 계약.

## 자동 검증

- `/api/v1/ai` 세션 → 첫 질문 → SSE → 요약 확인 → 전체 run 접수
- `message_id`, `revision`, `idempotency_key`, 프로젝트 범위, 세션당 활성 run
- 구형 `/v1`, asset, export, run 조회·retry 경로 제거
- `normal_price`, `product_count`, `asset_id`, 미정의 DTO 필드 거부
- 리워드 `quantity`와 표시 개수 분리, `price`만 렌더링
- 이미지 슬롯 내부 재시도와 `partially_succeeded`/`failed` callback
- 이미지 호출 동시성 상한·429 즉시 신규 호출 대기/동시성 축소·실패 슬롯 우선 재시도·실행 예산·완료 역전 시 슬롯 대응
- PostgreSQL 공유 페이싱·병렬 429의 중복 백오프 방지·연속 성공 후 회복·worker lease 갱신/만료·DB 장애 시 호출 차단
- `Retry-After`/`RetryInfo` 하한 준수·재시도 가능 오류 구분·실행 예산 초과 대기 시 미완료 슬롯 보존
- 성능 판정 독립 검증: 빈 텍스트·누락 영역·부분 성공·원본 재사용·중복/빈 이미지·미해결 슬롯·PNG/HTML 누락은 실패
- PNG 블록 병렬 렌더링·실패 블록만 재렌더링·최종 템플릿 순서 유지
- 추가 정보 채팅 요약 → revision 확인 → 실제 PNG 렌더링 → 하단 HTML → 완료 callback 연결
- HTML 허용 태그·입력 escape·빈 선택 항목 생략·리워드 상세 출력 제외
- BE 업로드 대상과 성공·실패 슬롯 불변식
- Funding Story run에 입력 snapshot·생성 문서·이미지 바이트 미저장
- LangGraph PostgreSQL checkpoint 미사용
- Content Insights 독립 artifact 상태·queue·retry 회귀 및 schema v2 STORYLINE 두 블록·내부 role 비노출
- OpenAPI와 체크인된 `docs/openapi.json` 일치
- 실제 Chromium·Konva·Pretendard PNG 렌더링
- PostgreSQL 17 Testcontainers 기반 TTL·lease·공유 페이싱·Content Insights migration/repository 검증

```bash
uv run ruff check src tests scripts
uv run pytest -q
uv build
```

| 실행 결과 (2026-09-23) | 결과 |
|---|---|
| `pytest -q` | 152 passed; Starlette 의존성 deprecation warning 1건 |
| `ruff check src tests scripts` | 통과 |
| `uv build` | sdist·wheel 생성 통과 |

## 운영 모델 로컬 전체 생성 (2026-09-23)

| 항목 | 결과 |
|---|---|
| 실행 | `local_openai_smoke` · 실제 Gemini 원고 + OpenAI 이미지 + Chromium + 로컬 BE 수신기 |
| 시간 | **137.129초**; 원고 61.981초 · 이미지 69.824초 · 렌더링 4.921초 |
| 완전성 | 텍스트 61/61 · 모델 이미지 18/18 · PNG 12/12 · 하단 HTML · 실패 슬롯 0 |
| 이미지 호출 | `gpt-image-2.5-flare` 18/18 성공 · 429/재시도 0 |
| 판정 | `succeeded` · 완전성 감사 통과 · `target_met=true` |
| 비용 | OpenAI 이미지 약 $0.370; Gemini 원고 비용 제외 |

단일 로컬 표본이며 배포 polling·실제 BE/S3/FE 왕복, 운영 WIF, 부하·품질 상한은 검증하지 않았다. 산출물은 로컬 `output/playwright/openai-postgres-smoke-20260923-02/`에만 보관한다.
이 산출물은 리워드 상세 제거 전 실행 기록이다. 현재 하단 HTML에서 리워드 상세를 제외한 변경은 별도 자동 테스트로 검증한다.

## 공유 재시도·페이싱 실측 (2026-09-22)

| 항목 | 결과 |
|---|---|
| 실행 | 기존 가상 제품 입력·실제 모델·실제 PNG 렌더러·로컬 BE 수신기, 전체 1회 |
| 전체 시간 | **913.938초 (15분 13.9초)**; 원고·모든 재시도/페이싱 대기·PNG·로컬 업로드/완료 응답 포함 |
| 완전 생성 | 생성 대상 텍스트 61/61 · 이미지 18/18 · PNG 12/12 · 하단 HTML 5개 정보와 리워드 상세 |
| 5분 판정 | **미달**; 300초 시점 이미지 7/18, `target_met=false` |
| 이미지 호출 | 43회 = 성공 18회 + 429 25회 |
| UI 직접 확인 | PNG 12개 로딩·폭 860px·하단 HTML 순서·리워드 표시·가로 넘침 없음 |
| 보관 | 실행 수치: [JSONL](evidence/runtime-2026-09-22-paced.jsonl); 최종 PNG/HTML: 로컬 `output/playwright/funding-story-paced-20260922-01/` (Git 제외) |
| 미검증 | 배포 API/DB polling 대기·실제 BE/S3/FE 왕복·다중 실제 생성 부하·전체 입력 규모의 5분 달성 |

- 템플릿·블록/슬롯 수·모델·해상도·입력 사실 축소 없음. 과거 원고/생성 이미지 재사용·별도 사전 생성·5분 강제 성공 처리 없음.
- 같은 run에서 이미 성공한 슬롯은 메모리에서 유지하고 실패 슬롯만 재시도. 부분 성공을 완전 성공으로 집계하지 않음.
- 생성 텍스트의 실제 scene 반영·고정 라벨 보존·각 이미지의 실제 모델 응답 해시·원본과의 차이·PNG 업로드·HTML 정보를 검증. 기존 비표시 고정 라벨은 생성 대상이 아니며 변경하지 않음.
- 실측 후 완전 단색/투명 이미지 및 scene 영역 삭제를 거부하는 감사 테스트를 추가했다. 원시 실측 기록을 이 추가 검사까지 수행한 것처럼 소급 변경하지 않음.
- 테스트 전용 가상 시계·짧은 간격은 단위 테스트에만 사용. 해당 실측 당시에는 Redis·기본 요청 간격 5초·최대 20초·실행 예산 3600초를 사용했다.
- 이전 Lite 실행보다 179.508초 짧았으나 429는 9→25회 증가. 단일 표본이므로 안정성 개선·운영 상한·5분 보장을 주장하지 않음. 품질은 이번 성능 판정에서 제외.

상세 수치·제약·재현: [생성시간 측정](funding-story-runtime-benchmark.md).

## 과거 Google 이미지 모델 비교 실측 (2026-09-22)

> 현재 운영 프로필의 `gpt-image-2.5-flare` 실측이 아니다. `local_google_experiment`에서 `gemini-3.1-flash-image`와 `gemini-3.1-flash-lite-image`를 비교한 개발 기록이다.

같은 가상 제품 입력·12개 블록·이미지 18개. 실제 모델·PNG 렌더러, 로컬 BE 수신기. 이미지 동시성 2·최대 3회·기존 429 대기 정책 유지. 각 모델 전체 실행 1회 비교이며 원고·이미지 프롬프트는 각 실행에서 새로 생성했다.

| 항목 | `gemini-3.1-flash-image` | `gemini-3.1-flash-lite-image` |
|---|---:|---:|
| 최종 결과 | `succeeded` · PNG 12/12 | `succeeded` · PNG 12/12 |
| 전체 시간 | 1,045.845초 (17분 25.8초) | 1,093.446초 (18분 13.4초) |
| 원고 단계 | 87.373초 | 50.146초 |
| 이미지 단계 (대기 포함) | 952.436초 | 1,038.145초 |
| 성공 이미지 호출 평균 | 10.513초 | 5.107초 |
| 이미지 429 | 9회 | 9회 |
| 이미지 재시도 대기 | 766.440초 | 944.750초 |
| 원고 재시도 대기 | 0초 | 15초 (429 1회) |
| 전체에서 재시도 대기만 제외한 환산값 | 279.405초 | 133.696초 |

- Lite 단일 이미지 사전 호출 성공: 6.180초. 위 전체 실행 집계에는 미포함.
- Lite 기본값·환경변수 재정의·실제 SDK 요청 모델 전달 자동 검증. BE/FE DTO·저장 방식·재시도 정책 변경 없음.
- Playwright 로컬 미리보기 검증: 폭 860px PNG 12개 전체 로딩, 하단 HTML이 마지막 이미지 다음에 배치됨, 필수 본문 제목·리워드 설명 표시, 가로 넘침 없음. 실제 FE 통합 검증은 아님.
- 단일 실행에서 Lite 성공 호출은 빨라졌지만 전체 완료는 47.601초 증가. 429 감소·운영 최대 시간·일반적인 속도 향상을 보장하지 않음. 대기 제외 환산값은 재시도 없는 실행을 직접 측정한 값이 아님.
- 시각 검토: 12개 PNG 생성 확인. 기존 리워드 빈 테두리·근거 없는 비교 문구가 남아 있으며, 일부 제품 부품 형상과 구성품 표현에도 편차가 있음. 모델 교체만으로 품질 문제가 해결됐다고 판단하지 않음.
- 로컬 결과: `output/playwright/funding-story-lite-20260922-01/`의 `result.json`, `execution.jsonl`, `completion.json`, `index.html`, 최종 PNG. 이전 비교 실행은 `funding-story-full-20260922-02/`. 수동 검토용 로컬 산출물이며 Git에 포함하지 않음.

## 별도 통합 검증 필요

- 배포된 BE의 `/internal/ai/media/upload-targets` 실제 presigned PUT
- 동일 run 완료 callback 재전송과 BE 객체 검증·상태 하향
- FE의 `partially_succeeded` 표시·실패 슬롯 안내·전체 재생성
- FE 결과 미리보기·에디터 불러오기·저장·구매자 상세의 HTML 표시·서식 왕복 보존
- 배포 환경의 `gpt-image-2.5-flare` 품질·비용·rate limit과 EKS WIF
- Chromium worker 동시성·메모리·timeout

추가 Funding Story 데이터베이스·테이블·ERD는 이 변경 범위에 없다.

`test_generation_flow.py`는 로컬 대체 모델·업로드 수신기와 실제 Chromium/PNG를 연결하고, PNG 뒤 HTML을 브라우저 DOM에서 검사한다. 실제 FE 화면·운영 모델 속도를 검증한 결과는 아니다.

## Content Insights 별도 검증 범위

- `POST /api/v1/ai/content-insight-runs` 접수와 parent·artifact 멱등성
- `PAGE_SUMMARY`와 `STORYLINE`의 독립 상태·queue·retry 및 `required_artifacts_ready` 판정
- 최신 source revision이 아닌 결과의 stale 처리
- Project Service 범위를 벗어난 run 조회·artifact 재시도 거부
- 공개 응답에서 AI 내부 Storyline role을 제거하고 headline·description만 전달

## 완료로 주장하지 않는 범위

- 모든 제품군·입력 길이에서의 생성 품질과 생성 이미지의 제품 형상 재현성
- 참조 서비스와의 픽셀 단위 동일성
- Docker 이미지·배포된 AI와 Project Service 사이의 실제 HTTP smoke test
- 운영 부하, Gateway·S3·CDN·게시 연동, Chromium 동시성·리소스 한계
- 운영 이미지 모델 `gpt-image-2.5-flare`·텍스트 모델 `gemini-3.8-flash` 품질·비용 평가와 Content Insights 실제 모델 품질 평가
- queue 경보 임계값, 보존 기간, 비용 metric의 운영 보정
