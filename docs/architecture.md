# Funding Story AI 실행·API 계약

기준: 2026-09-16, API 0.2.0. 이 저장소는 LangGraph·AI API와 호출 계약을 소유한다. FE 화면·디자인·Tiptap·Polotno 및 BE 프로젝트 본문 저장 코드는 범위에 포함하지 않는다.

## 책임 경계

| 영역 | 소유 | 기준 |
|---|---|---|
| 사용자·프로젝트·선물·권한 | BE | AI 호출 전 소유권 확인, 등록 사실 제공 |
| 대화·확인 강점·생성 작업 | AI | PostgreSQL 세션·작업·LangGraph 체크포인트 |
| 임시 디자인 | AI | PNG 내보내기와 BE 저장 확인 전까지만 유지 |
| PNG·생성 이미지 파일 | 인프라 저장소 | AI가 저장, BE가 프로젝트 본문과 연결 |
| 최종 PNG 순서·대체 텍스트·하단 본문 | BE | FE가 AI 결과를 불러온 뒤 revision 저장 |
| 화면·모달·본문 편집 | FE/디자인 | 본 저장소는 요청·응답 계약과 샘플 제공 |

호출 경로는 FE → BE → AI다. AI는 내부 Bearer 토큰과 `X-Project-Id`를 요구하며 사용자 인증을 직접 대체하지 않는다. FE가 AI 토큰을 보유하거나 AI API를 직접 공개 호출하면 안 된다.

## 상태 흐름

```text
session
  → chat queued/running/succeeded|failed
  → review confirmed(revision)
  → run queued/running/succeeded|partially_succeeded|failed
  → export rendering/succeeded|failed
  → BE 본문 저장
  → export committed + 임시 디자인 정리
```

`succeeded` run만 PNG로 내보낼 수 있다. 1~2종 선물에서 남는 고정 템플릿 이미지 슬롯의 `input_required`는 중립색 편집 대기 프레임으로 렌더링하므로 export를 막지 않는다. 실제 이미지 생성 `failed`가 남은 `partially_succeeded` 결과는 409로 거부한다. 저장 성공 전에는 commit을 호출하지 않는다.

## API

모든 `/v1` 요청은 내부 Bearer 토큰과 `X-Project-Id`를 요구한다.

| 메서드·경로 | 역할 |
|---|---|
| `POST /v1/assets` | 로컬/내부 검증용 multipart 이미지 업로드 |
| `POST /v1/assets/import` | BE가 검증한 프로젝트 S3 key 가져오기 |
| `GET /v1/assets/{id}` | 프로젝트 범위 자산 조회 |
| `POST /v1/sessions` | 등록 정보 입력 스냅샷 생성 |
| `GET /v1/sessions/latest` | 프로젝트의 최근 세션 복구 |
| `GET /v1/sessions/{id}` | 세션·대화·review 조회 |
| `POST /v1/sessions/{id}/start` | 등록 정보를 읽고 AI가 첫 추가 질문을 시작 |
| `POST /v1/sessions/{id}/messages` | 메시지·첨부 접수, 대화 작업 생성 |
| `GET /v1/chats/{id}/events` | 사용자에게 보여줄 답변 SSE |
| `POST /v1/sessions/{id}/confirm` | 최신 revision의 요약·핵심 강점 확인 |
| `POST /v1/runs` | 확인된 입력으로 전체 페이지 생성 |
| `GET /v1/runs/{id}` | 생성 상태·임시 후보 조회 |
| `POST /v1/runs/{id}/retry` | 실패/부분 성공 슬롯 재시도 |
| `POST /v1/runs/{id}/exports` | 문구 수정값 적용, 블록별 PNG+텍스트 결과 생성 |
| `GET /v1/exports/{id}` | 내보내기 결과 복구 |
| `POST /v1/exports/{id}/commit` | BE 저장 revision 확인, 임시 디자인 정리 |

정확한 필드와 오류 응답은 [OpenAPI](openapi.json)를 따른다.

## FE 호출에 필요한 핵심 계약

처음 AI 작성을 열 때 FE는 BE context로 세션을 만든 뒤 `POST /v1/sessions/{id}/start`를 호출한다. 첫 AI 호출은 등록된 기본 정보·선물·이미지를 먼저 읽고 제품명·카테고리·가격처럼 이미 있는 내용을 다시 묻지 않는다. 확인한 내용을 짧게 되짚은 뒤 제작 계기·강조할 사용 장면·대상·말투 중 실제로 비어 있는 1~2가지만 질문한다. 이 첫 답변은 사용자 수정으로 취급하지 않는다.

메시지는 `message_id`와 현재 `revision`, 생성은 `idempotency_key`와 확인된 `revision`을 보낸다. 같은 키에 다른 내용은 409다. 메시지 후 응답 완료 revision을 다시 조회해 확인해야 한다. 애니메이션 시간이 아니라 `run.status`로 완료를 판단한다.

PNG 내보내기 요청은 다음 형태다.

```json
{
  "source_input_revision": 3,
  "idempotency_key": "project-uuid:export:1",
  "text_overrides": {"hero.detail-0": "제약 없는\n무선 청소"}
}
```

응답의 `images`는 `block_id`, `order`, `asset_id`, `width`, `height`, `alt`를 가진다. `information`은 예산·일정·팀·정책·예상 어려움 입력을 구조화한 일반 텍스트다. `fixed_content.crowdfunding_notice_key`는 공통 안내 컴포넌트를 가리키며 LLM 생성 문구가 아니다. `project_summary`는 후속 라이브커머스 입력이다.

`text_overrides`는 존재하는 텍스트 슬롯만 허용하며 레이아웃·이미지·도형 변경을 받지 않는다. 최종 PNG의 디자인 재편집과 부분 블록 재생성 API는 제공하지 않는다.

BE가 PNG 자산과 하단 본문을 같은 저장 revision으로 확정한 뒤 다음을 호출한다.

```json
POST /v1/exports/{export_id}/commit
{"document_revision": 7}
```

동일 revision의 재호출은 안전하다. 다른 revision으로 이미 commit된 export는 409다. commit은 run의 임시 `document`·입력 `snapshot`, 합성에 사용한 중간 생성 이미지와 해당 실행의 LangGraph 체크포인트를 정리한다. 사용자 입력 이미지와 최종 PNG 자산·순서·대체 텍스트·정보 텍스트는 남는다. 정리가 실패하면 `cleanup_pending=true`를 유지하고 저장 결과를 취소하지 않는다.

## LangGraph와 재시도

- 대화: 등록 정보·전체 대화·이미지를 바탕으로 질문 또는 요약·제품의 핵심 강점을 구조화한다.
- 생성: 블록 선택 → 슬롯 원고 → 슬롯별 이미지 → 임시 디자인 조립.
- JSON 계약 오류: 오류 정보를 같은 단계에 전달해 최초 호출 뒤 최대 2회 재생성.
- 공급자 429/503: 공식 SDK 래퍼에서 15초·30초 대기 후 제한 재시도.
- 이미지 재시도: 성공 슬롯과 생성 자산 재사용.
- 별도의 사실·표현 품질 심사 LLM이나 자동 교정 단계는 없다.

가격·수치·단위·사양은 입력 원문을 보존한다. 사용자의 최신 명시 수정은 AI 초안 입력에 반영하지만 BE의 등록 원본을 직접 변경하지 않는다.

## 저장과 관측

Redis는 Celery 전달용이며 결과 원본이 아니다. PostgreSQL이 세션·작업·idempotency·체크포인트의 기준이다. LangSmith는 선택적 분석 도구이며 복구 의존성이 아니다. 로그와 trace에는 서비스 토큰·서명 URL·이미지 바이트를 기록하지 않는다.

최종 파일 저장소와 보존 기간은 인프라팀이 확정한다. local backend는 검증용이다. 운영 S3에서는 BE가 프로젝트 자산을 검증하고 AI에는 승인된 key만 전달한다.

## 후속 타팀 작업

- FE: 기존 FundingStoryModal의 목업 reducer를 API controller로 교체, SSE·폴링·오류 상태 처리.
- FE: export `images`와 `information`을 기존 Tiptap 본문 형식으로 불러오고 저장 성공 뒤 commit 호출.
- BE: 위 API 중계, 프로젝트 자산 영구 연결, 본문 revision 저장과 commit 순서 보장.
- 디자인·기획: 결과 모달 문구 수정 UI, `input_required`, 실패/재시도, 공통 크라우드 펀딩 안내 문안 확정.
- 인프라·보안: Gateway, S3 권한, API/worker 배포, 보존 기간, LangSmith 전송 범위 확인.

## 템플릿 구성 계약

`resources/template.json`의 `composition`이 포함 여부와 순서를 소유한다.
필수 순서는 hero → problem → transition → product-visual → positioning → product-gallery → comparison → promise → Point → rewards다.

- Point: 확인된 강점당 1개. 3~12개를 유지하며 전부 생략할 수 없다. 각 블록에 strengthId를 연결한다.
- Information: Review.include_information 기본 false. LLM은 입력에 있는 추가 제품 안내가 Point와 중복되지 않고 슬롯을 추정 없이 채울 수 있을 때만 선택하며 information_reason에 근거를 남긴다.
- Information은 하단 예산·일정·팀·신뢰와 안전 텍스트와 별개다. 하단 정보만 있다고 포함하지 않는다.
- 필수 내용이 부족하면 대화의 missing과 reply에서 보완을 요청한다. 가격·성능·비교 근거를 만들거나 필수 블록을 삭제해서 해결하지 않는다.
- 포함된 모든 블록의 텍스트·이미지 슬롯은 빠짐없이 작성한다. 디자인 좌표와 순서는 LLM 출력 대상이 아니다.

구조 회귀 테스트와 실제 14블록 생성·PNG 출력 사례를 확인했다. 완료 범위와 남은 한계는 [검증 기록](validation.md)을 따른다.

### Konva 텍스트 배치와 출력 기준

PNG export는 서버의 Playwright Chromium + Konva 10.5.0 + Pretendard로 수행한다. 템플릿 scene의 text/shape/image 속성을 그대로 전달한다. LLM이 폰트 크기·도형 크기를 바꾸지 않으며, 짧은 텍스트를 채우기 위해 확대하지 않는다. 실행 시 외부 CDN을 호출하지 않는다. 고정 Konva 파일과 라이선스는 `resources/vendor`에 포함한다.

- 템플릿 `textFlows`: promise의 강조 문구와 후속 문구는 같은 줄의 묶음이다. 시작점·최대폭은 고정하고 강조 텍스트의 실제 Konva 너비 + 4px에 후속 문구를 배치한다. 다른 노드는 이동하지 않는다.
- `copyFit`: hero/rewards 제목 및 promise 원 안 문구는 2줄, 연결 제목 각 조각은 1줄, hero detail은 최대 2줄. requirements에 명시하고 실제 Konva 줄바꿈으로 확인한다.
- LangGraph 출력 형식 검사에서 폭·높이·명시 줄 수를 측정한다. 실패하면 해당 슬롯과 실제/허용 값을 재시도에 전달한다. 별도 사실 판정 LLM 또는 생성 후 자동 교정 단계는 두지 않는다. 숫자·단위 축약으로 맞추지 않는다.
- 출력은 PNG+일반 텍스트 API다. FE 변경이나 Konva 에디터 도입은 범위에 포함하지 않는다. `textLayoutVersion: 1`을 scene에 기록한다.
- Chromium 설치가 필요하다: 로컬 `uv run playwright install chromium`, Linux `uv run playwright install --with-deps chromium`. Dockerfile에도 설치 단계를 포함한다. 브라우저는 export당 1개이며 모든 블록을 출력 후 닫는다. 운영 동시성·메모리 부하 측정은 별도다.

속성 기준: [Konva.Text 공식 문서](https://konvajs.org/api/Konva.Text.html), [Playwright 브라우저 설치](https://playwright.dev/python/docs/browsers).

선물 상세 설명(`gift_details`)은 텍스트 생성·추가 수집·출력 대상에서 제외한다. 입력에 해당 키가 있어도 조립 및 export에서 제외한다. 리워드 디자인 블록은 유지한다.
