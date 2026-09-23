# Funding Story AI 기능 연동 인계

관점: AI팀 일반이 아닌 **Funding Story AI 기능 담당자**.

## AI 제공 범위

| 항목 | 제공 내용 |
|---|---|
| API | `/api/v1/ai` 세션·채팅·확인·전체 생성 |
| 상태 | PostgreSQL TTL 단기 상태, 공개 ID는 `session_id/chat_id/run_id` |
| 입력 | BE Core DTO의 프로젝트·리워드·단기 이미지 읽기 참조 |
| 출력 | BE 업로드 대상에 PNG PUT 후 완료 callback |
| 실패 | 내부 이미지 재시도, 부분 성공·전체 실패 전달 |
| 제외 | AI asset/export API, 부분 재생성, AI 결과 조회, 영구 결과 저장 |

## BE 필수 연동

1. FE의 `/api/v1/ai/...` 요청을 사용자·프로젝트 권한 확인 후 같은 path로 AI에 전달한다.
2. 세션 생성·run 생성 시 BE Core DTO를 `FundingStoryContext`로 구성한다.
3. 확인 시점과 생성 시점의 Core fingerprint가 다르면 재확인을 요구한다.
4. `/internal/ai/media/upload-targets`에서 프로젝트 경로의 presigned PUT을 슬롯별로 발급한다.
5. `/internal/ai/runs/{run_id}/completion`에서 URL·prefix·객체 존재·MIME·크기를 검증한다.
6. 검증한 본문·URL·최종 상태를 BE 공개 run에 저장한다.
7. `partially_succeeded`를 유효한 terminal status로 FE에 전달한다.

## FE 필수 연동

1. AI가 아닌 BE의 `/api/v1/ai/...`만 호출한다.
2. `message_id`, 현재 `revision`, run `idempotency_key`를 관리한다.
3. `succeeded`, `partially_succeeded`, `failed`를 모두 terminal 상태로 처리한다.
4. 재생성은 슬롯 지정을 제거하고 전체 `POST /runs`로 요청한다.

## 데이터베이스

Funding Story AI를 위한 추가 테이블·ERD는 없다. 프로젝트 사실·최종 본문·최종 이미지 참조는
BE 기존 소유 구조에 저장하며, AI는 TTL 상태 외 결과를 보관하지 않는다. BE 기존 구조로 완료
callback 결과를 수용할 수 없는 경우에만 BE 담당자가 별도 설계를 제안한다.

## 운영 PostgreSQL

| 항목 | 계약 |
|---|---|
| 대상 | Content Insights 결과와 Funding Story 단기 상태·작업 전달·호출 제어 |
| local/test | Compose·Testcontainers의 PostgreSQL 17; 운영 배포 대상 아님 |
| dev/prod | 기존 AWS EKS CNPG의 Content Insights용 AI 논리 DB 사용; 별도 PostgreSQL 서버·컨테이너 생성 없음 |
| 구현 | 기존 `ai_records`·`ai_requests`의 TTL row·DB polling·row/advisory lock·lease 사용 |
| AI | Flyway migration·schema·query·polling worker 소유 |
| 인프라·GitOps | CNPG·접속 Secret·백업·연결 한도 소유 |
| BE | Project Service DB 제공·공유 없음; 내부 API와 callback만 연동 |
