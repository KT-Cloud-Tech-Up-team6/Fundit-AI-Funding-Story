# 검증 범위

기준: 2026-09-18, AI API 0.3.0. 이 문서는 저장소에서 확인한 범위와 아직 확인하지 않은 범위를 구분한다.

## 자동 검증

- Ruff: `src`, `tests`, `scripts` 검사.
- pytest: 77개 통과. 모델 호출은 mock이며 DB 통합 테스트는 Testcontainers PostgreSQL, 렌더링 테스트는 실제 Chromium을 사용.
- in-memory repository fake로 session → chat → confirm → run과 idempotency 충돌을 Docker/DB 없이 검증.
- application/domain의 framework·infrastructure 독립성과 runtime composition root 경계를 자동 검사.
- 빈 PostgreSQL 17에 Flyway V1/V2 적용, 재실행 무변경, 기존 schema 검사 후 baseline, checkpoint migration 0~9 기준을 확인.
- 50개 동시 query가 설정된 psycopg pool을 통해 완료되고 readiness가 최신 schema version을 확인.
- 세션 진입·메시지·SSE·확인 revision·idempotency·프로젝트 자산 격리.
- 필수 블록과 선택 Point·Information 구성, 실패 이미지 슬롯만 재시도.
- PNG export·BE 저장 commit·임시 데이터 정리·이미지 자산 수명주기.
- Konva 연결 문구 간격, 실제 줄 수, 2줄 계약, PNG 크기.
- 입력에 `gift_details`가 있어도 조립·export에서 제외.
- wheel/sdist에 템플릿·Konva JS·라이선스 포함, OpenAPI 코드 일치.
- Funding Story 세션과 무관한 Content Insights 생성 API, 두 artifact의 독립 상태·필수 여부·재시도·멱등성·revision stale 처리.
- `PAGE_SUMMARY`와 `STORYLINE`의 별도 mapper·prompt·queue routing, broker 장애 재전달 경로, 중복 delivery lock.
- 이미지·동영상 URL을 거부하는 snapshot 계약과 불충분 입력, prompt injection, 수치·인증·배송·환불 정책 경계 fixture.
- Content Insights 요청·응답 OpenAPI 예시와 저장된 `docs/openapi.json`의 코드 일치.

실행법과 환경은 [개발 안내](development.md)를 따른다. GitHub Actions에서도 Testcontainers와 production Flyway migration, Chromium·폰트 설치, LangGraph schema drift 검사, Ruff, pytest, 패키지 빌드를 실행한다.

## 실제 모델·출력 사례

가상 무선청소기 LUMI S1 입력 한 건으로 대화 요약 → 확인 → 생성 → PNG export를 확인했다. 결과는 필수 9블록 + Point 4개 + Information의 14블록이다.

- 생성 이미지 17개 성공. 429가 발생한 슬롯은 간격을 두고 해당 슬롯만 재시도했다.
- 등록 선물 1종 외 2카드는 값을 만들지 않고 `input_required`로 유지했다.
- 수치·단위 원문 보존과 측정 조건 표기를 확인했다. 런타임 자동 사실 교정 단계는 없다.
- Chromium/Konva/Pretendard 출력에서 슬롯 높이·명시 폭·줄 수 위반 0건을 확인했다.
- 브라우저 1280px와 390px에서 PNG 14개 로드 및 가로 넘침이 없음을 확인했다.

생성 사례의 로컬 실행 ID·출력 파일·스크린샷은 저장소에 포함하지 않는다.

Content Insights의 `page-summary-v1`과 `storyline-v2`는 mock 모델로 계약을 검증했다. Storyline은 두 section의 headline·description 한 줄, 고정 순서, 내부 역할명 미노출, 개조식 문체를 자동 검증한다. 실제 모델 출력은 대표 프로젝트 5건의 간이 기준으로 배포 전에 평가한다.

## 아직 완료로 보지 않는 항목

- 모든 제품군·입력 길이에서의 생성 품질.
- 생성 이미지의 제품 세부 형상 정밀 재현.
- 참조 서비스와 모든 픽셀의 동일성.
- Docker 이미지 실행.
- 운영 부하·Chromium 동시성·Gateway·S3·CDN·게시 통합.
- 배포된 AI와 Project Service 사이의 실제 HTTP smoke test와 운영 부하 검증.
- Content Insights 실제 모델 출력 간이 품질 평가 실행, queue 경보 임계값 보정, 보존 기간과 비용 metric.

공통 크라우드 펀딩 안내 key `fundit.crowdfunding-notice.pending-v1`은 운영 문안 확정 전 값이다.
