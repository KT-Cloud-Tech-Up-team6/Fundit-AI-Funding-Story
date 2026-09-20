# Funding Story FE 호출 계약

FE는 Funding Story AI를 직접 호출하지 않는다. 모든 `/api/v1/ai/...` 요청은 BE에 보내며,
BE가 사용자·프로젝트 권한을 검사한 뒤 같은 path로 AI를 호출한다.

| FE 기능 | FE → BE | 비고 |
|---|---|---|
| 세션 복구 | `GET /api/v1/ai/sessions/latest` | TTL 만료 시 `session=null` |
| 세션 생성 | `POST /api/v1/ai/sessions` + `{}` | BE가 Core context를 구성해 AI에 추가 |
| 첫 질문 | `POST /api/v1/ai/sessions/{session_id}/start` | `chat_id` 반환 |
| 답변 전송 | `POST /api/v1/ai/sessions/{session_id}/messages` | `message_id/revision/text` |
| 응답 수신 | `GET /api/v1/ai/chats/{chat_id}/events` | `message` 누적 text, `done` terminal event |
| 요약 확인 | `POST /api/v1/ai/sessions/{session_id}/confirm` | 현재 `revision` |
| 전체 생성 | `POST /api/v1/ai/runs` | `session_id/confirmed_revision/idempotency_key` |
| 생성 조회 | `GET /api/v1/ai/runs/{run_id}` | BE 소유 API, AI로 전달하지 않음 |

FE가 생성하거나 전달하지 않는 필드:

- `FundingStoryContext`, `ProjectFact`, `RewardFact`, `SourceImageRef`
- Core fingerprint
- AI 내부 업로드 URL·callback DTO
- `asset_id`, `export_id`, `target_block_id`

상태는 `queued`, `running`, `succeeded`, `partially_succeeded`, `failed`를 처리한다. 부분 성공은
성공 결과와 실패 슬롯을 함께 표시할 수 있어야 한다. 재생성은 실패 슬롯만 보내지 않고 새
`idempotency_key`로 전체 `POST /runs`를 호출한다.

## 오류·연결 처리

| 응답 | FE 처리 |
|---|---|
| `409 CONFLICT` · revision/fingerprint | 세션을 다시 조회하고 최신 summary를 확인한 뒤 재요청 |
| `409 CONFLICT` · 활성 run/idempotency 충돌 | 기존 run을 조회하고 같은 입력이면 결과를 재사용 |
| `401`/`403`/`404` | 로그인·프로젝트 권한·리소스 존재 여부를 BE 기준으로 표시 |
| `422`/`429`/`503` | 입력 미완성 또는 일시적 의존성 오류를 구분해 보완·재시도 안내 |

SSE가 끊기면 새 `message_id`로 자동 재전송하지 않는다. 먼저 기존 chat과 세션 상태를 조회하고,
동일한 입력을 다시 접수해야 할 때만 같은 `message_id`를 재사용한다. 생성 완료 여부는 로딩
애니메이션이 아니라 `run.status`와 `GET /api/v1/ai/runs/{run_id}` 응답으로 판단한다.

## Content Insights 공개 상세

FE는 Content Insights AI endpoint를 직접 호출하지 않고 Project Service가 저장한 canonical
결과를 조회한다. 공개 결과는 Funding Story의 정보 수집 요약이나 생성 run과 구분한다.

| type | 의미 | 생성 기준 | 미존재 처리 |
|---|---|---|---|
| `PAGE_SUMMARY` | 프로젝트 상세 페이지 요약 | 등록 프로젝트 snapshot | 필수 결과 준비 전 공개·준비 상태 정책 적용 |
| `STORYLINE` | 프로젝트의 What·Why 요약 | 등록 시 필수 Storyline artifact | 성공 시 두 개의 제목·설명 블록 표시 |
| `LIVE_SUMMARY` | 종료 라이브 요약 | 종료된 라이브 결과 | 라이브 미진행 시 생략 |

`STORYLINE` 내부의 의미 구분명은 FE에 노출하지 않는다. Project Service가 role을 제거한
`headline`·`description`을 순서대로 렌더링한다. 사용자에게 보여주는 요약 확인 메시지와
Content Insights canonical 결과를 같은 데이터로 취급하지 않는다.
