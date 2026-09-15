# Funding Story AI 실행·API 계약

기준: 2026-09-16, API 0.2.0. 이번 저장소는 LangGraph·AI API와 호출 계약을 소유한다. FE 화면·디자인·Tiptap·Polotno 및 BE 프로젝트 본문 저장 코드는 수정 범위가 아니다.

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

`succeeded` run만 PNG로 내보낼 수 있다. 이미지 슬롯의 `input_required` 또는 `failed`가 남은 `partially_succeeded` 결과는 409로 거부한다. 저장 성공 전에는 commit을 호출하지 않는다.

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

메시지는 `message_id`와 현재 `revision`, 생성은 `idempotency_key`와 확인된 `revision`을 보낸다. 같은 키에 다른 내용은 409다. 메시지 후 응답 완료 revision을 다시 조회해 확인해야 한다. 애니메이션 시간이 아니라 `run.status`로 완료를 판단한다.

PNG 내보내기 요청은 다음 형태다.

```json
{
  "source_input_revision": 3,
  "idempotency_key": "project-uuid:export:1",
  "text_overrides": {"hero.detail-0": "제약 없는\n무선 청소"}
}
```

응답의 `images`는 `block_id`, `order`, `asset_id`, `width`, `height`, `alt`를 가진다. `information`은 예산·일정·팀·정책·예상 어려움·선물 상세 입력을 구조화한 일반 텍스트다. `fixed_content.crowdfunding_notice_key`는 공통 안내 컴포넌트를 가리키며 LLM 생성 문구가 아니다. `project_summary`는 후속 라이브커머스 입력이다.

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
