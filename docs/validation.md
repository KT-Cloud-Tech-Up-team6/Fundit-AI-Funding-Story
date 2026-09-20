# 검증 범위

기준: 2026-09-21, Funding Story API 계약.

## 자동 검증

- `/api/v1/ai` 세션 → 첫 질문 → SSE → 요약 확인 → 전체 run 접수
- `message_id`, `revision`, `idempotency_key`, 프로젝트 범위, 세션당 활성 run
- 구형 `/v1`, asset, export, run 조회·retry 경로 제거
- `normal_price`, `product_count`, `asset_id`, 미정의 DTO 필드 거부
- 리워드 `quantity`와 표시 개수 분리, `price`만 렌더링
- 이미지 슬롯 내부 재시도와 `partially_succeeded`/`failed` callback
- BE 업로드 대상과 성공·실패 슬롯 불변식
- Funding Story run에 입력 snapshot·생성 문서·이미지 바이트 미저장
- LangGraph PostgreSQL checkpoint 미사용
- Content Insights 독립 artifact 상태·queue·retry 회귀 및 schema v2 STORYLINE 두 블록·내부 role 비노출
- OpenAPI와 체크인된 `docs/openapi.json` 일치
- 실제 Chromium·Konva·Pretendard PNG 렌더링
- PostgreSQL 17 Testcontainers 기반 기존 Content Insights migration·repository 검증

```bash
uv run ruff check src tests scripts
uv run pytest -q
uv build
```

## 별도 통합 검증 필요

- 배포된 BE의 `/internal/ai/media/upload-targets` 실제 presigned PUT
- 동일 run 완료 callback 재전송과 BE 객체 검증·상태 하향
- FE의 `partially_succeeded` 표시·실패 슬롯 안내·전체 재생성
- 운영 Google 모델 품질·비용·rate limit
- Chromium worker 동시성·메모리·timeout

추가 Funding Story 데이터베이스·테이블·ERD는 이 변경 범위에 없다.

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
- 운영 Google 모델 품질·비용 평가와 Content Insights 실제 모델 품질 평가
- queue 경보 임계값, 보존 기간, 비용 metric의 운영 보정
