# 프론트엔드 호출 대응표

이 문서는 2026-09-16 `Fundit-FE` main의 `ProjectStoryForm → StoryEditor → FundingStoryModal` 화면 구조와 디자인을 유지하는 목표 호출 계약이다. AI 저장소는 FE 연결 완료를 보장하지 않는다. FE 연동 시 별도 AI 작성 화면을 추가하지 않고 모달 내부의 목업 reducer와 timer를 API 어댑터로 교체한다.

선택적 Funding Story 작성 흐름과 공개 상세의 Content Insights 조회 흐름은 분리한다. FE는 Content Insights AI API를 직접 호출하지 않고 Project Service가 저장한 canonical 결과만 조회한다.

| 기존 화면 동작 | AI API | FE가 보관할 값 | 상태·주의점 |
|---|---|---|---|
| 모달 열기 | `GET context`는 BE 책임, 이후 `GET /sessions/latest` 또는 `POST /sessions` | session ID·revision | 등록 사실을 중복 질문하지 않음 |
| 이미지 첨부 | BE upload URL → `POST /assets/import`; 로컬만 multipart `/assets` | asset ID | 썸네일과 AI 참고 이미지는 별도 |
| 메시지 전송 | `POST /sessions/{id}/messages` | message ID·revision·chat ID | 같은 ID 재전송 가능, 다른 본문은 409 |
| 답변 표시 | `GET /chats/{id}/events` | 누적 reply | done 뒤 세션 재조회, 내부 review는 스트리밍하지 않음 |
| 요약·강점 표시 | `GET /sessions/{id}` | review·최신 revision | 수정·정렬·삭제는 새 메시지로 접수 |
| 그대로 생성 | `POST /sessions/{id}/confirm` → `POST /runs` | run ID·idempotency key | 확인 revision과 run revision 일치 필수 |
| 로딩 애니메이션 | `GET /runs/{id}` 폴링 | status·stage·이미지 수 | 애니메이션을 실제 완료율로 사용하지 않음 |
| 전체 재생성 | 새 idempotency key로 전체 `POST /runs` | 새 run ID | 부분 블록 재생성은 제외 |
| 결과 문구 수정 | 로컬 text override 편집 | node ID→text | 레이아웃·이미지 편집은 이번 범위 밖 |
| 불러오기 준비 | `POST /runs/{id}/exports` | export ID·PNG manifest·information | succeeded run만 가능 |
| 본문 저장 | BE 본문 저장 API | BE document revision | AI API가 저장 성공을 추정하지 않음 |
| 저장 성공 알림 | `POST /exports/{id}/commit` | committed 상태 | 임시 디자인 정리, 동일 revision 재호출 안전 |

## FE 연동 시 적용할 호출 계약

`FundingStoryModal`의 DOM·스타일·모달 전환은 유지한다. 서버가 질문과 요약을 결정하며 FE는 session revision, SSE 응답, run 상태를 표시한다. `onImport(body, html?)`의 두 번째 인자로 PNG 블록과 텍스트 섹션 HTML을 전달하고, `StoryEditor`는 이 값이 있으면 Tiptap 본문에 불러온다.

FE의 `/api/funding-story` 프록시와 BE 중계 API는 세션 생성 → 메시지/SSE → 확인 → 생성/조회 → PNG+텍스트 export 순서로 연결해야 한다. Tiptap 본문 저장 성공을 확인한 뒤에만 `commit`을 호출한다. 별도 런타임 화면이나 Konva 편집 화면은 이 계약에 포함하지 않는다.

## 권장 오류 표시

| 응답 | 화면 의미 |
|---|---|
| 409 revision | 다른 탭·응답으로 상태가 바뀜. 세션/작업을 다시 조회 |
| 409 run not succeeded | 실패 슬롯을 보여주고 retry 또는 입력 보완 |
| 410 temporary design cleared | 이미 저장된 본문을 BE에서 열기 |
| 422 unknown text slot | 오래된 UI/템플릿 계약. 사용자 입력을 버리지 않고 오류 표시 |
| 401/403/404 | 로그인·프로젝트 권한·삭제 상태를 BE 기준으로 처리 |
| 429/503 provider | AI 내부 제한 재시도 뒤 실패한 경우 재시도 UI 제공 |

SSE 단절 시 같은 메시지를 새 ID로 자동 재전송하지 않는다. 기존 chat 상태와 세션 메시지를 먼저 조회한다. 생성·export는 idempotency key를 재사용한다.

## 공개 상세의 요약 타입

Project Service는 같은 표시명을 반복하지 않고 최소한 다음 의미를 구분해 제공한다.

| type | 임시 표시명 | 출처 | 미존재 처리 |
|---|---|---|---|
| `PAGE_SUMMARY` | 페이지 요약 | 등록 프로젝트 snapshot 기반 Content Insights | 필수 결과 준비 전 공개/심사 gate 정책 적용 |
| `STORYLINE` | 스토리라인 | 등록 시 필수 Storyline artifact | 성공 시 두 headline·description 블록 표시, 준비 전 공개 화면에서 생략 |
| `LIVE_SUMMARY` | 라이브 요약 | 종료된 라이브 요약 | 라이브 미진행 시 항목 자체를 생략 |

Storyline의 두 블록은 내부적으로 리워드 정체성과 프로젝트 필요성을 구분하지만 Project Service가 role을 제거하므로 FE는 WHAT·WHY·DIFFERENCE를 표시하지 않는다. 각 블록의 첫 줄은 headline, 둘째 줄은 description으로 렌더링한다.

사용자 입력 확인 화면의 요약과 기존 Funding Story export `project_summary` preview는 위 공개 상세 데이터가 아니다. 현재 공개 화면은 `SUCCEEDED` 결과만 표시하고 `PENDING`, `FAILED`, `NOT_REQUESTED`, `STALE`은 생략한다.
