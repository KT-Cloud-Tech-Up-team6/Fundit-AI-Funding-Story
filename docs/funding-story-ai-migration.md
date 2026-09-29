# Funding Story AI — DB 마이그레이션

기존 PostgreSQL 서버 안의 **Funding Story AI용 논리 DB**에 적용합니다. 별도 DB 서버는 필요 없습니다. 현재 API·worker는 `public` 스키마와 `flyway_schema_history`를 사용하므로, BE 서비스의 DB와 분리된 `DB_NAME`이 필요합니다. 같은 DB에 이력 테이블 이름만 바꾸거나 다른 스키마를 지정하는 방식은 현재 코드에서 지원하지 않습니다.

## 이미지·실행

| 항목 | 값 |
| --- | --- |
| ECR | `899957568205.dkr.ecr.ap-northeast-2.amazonaws.com/fundit-ai-funding-story` |
| API·worker 태그 | `sha-<main 커밋 SHA>` |
| migration 태그 | `sha-<main 커밋 SHA>-migration` |
| 플랫폼 / Flyway | `linux/amd64` / `13.7.0` |
| 실행 | 이미지 기본 명령 `migrate` (`args: ["migrate"]`도 가능) |
| 실제 태그·digest | 해당 main Actions 실행의 Summary 및 `migration-image` 아티팩트 |

동일한 ECR 저장소에 별도 태그로 게시합니다. GitOps Job에는 digest로 고정할 수 있습니다. SQL은 이미지의 `/flyway/sql`에 포함되어 별도 마운트가 필요 없습니다.

## Job 환경변수

| 변수 | 설정 |
| --- | --- |
| `FLYWAY_URL` | `jdbc:postgresql://<DB_HOST>:<DB_PORT>/<DB_NAME>?sslmode=<DB_SSLMODE>` |
| `FLYWAY_USER` | AI용 DB의 migration 계정 (Secret 주입) |
| `FLYWAY_PASSWORD` | 해당 계정 비밀번호 (Secret 주입) |

`DB_NAME`은 API·worker와 동일하게 지정합니다. TLS 모드는 인프라 DB 설정에 맞춥니다. 이미지 기본 스키마는 `public`, 이력 테이블은 `flyway_schema_history`입니다. `baseline`·`repair`는 최초 적용에 사용하지 않습니다.

## 적용 대상·권한

| SQL | 대상 |
| --- | --- |
| V1 | `ai_records`, `ai_requests`: AI 데이터·임시 작업 상태·중복 요청 관리 |
| V2 | `checkpoint_migrations`, `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`: 현재 애플리케이션의 스키마 검증에 필요한 테이블 |

현재 Funding Story 그래프는 영구 checkpoint를 쓰지 않지만 준비 상태 검사가 V2를 요구하므로 함께 포함합니다. 기존 SQL은 수정하지 않습니다.

인프라팀은 AI용 DB·계정을 준비합니다. migration 계정에는 DB 접속과 `public` 스키마 사용·테이블/인덱스 생성 권한이 필요합니다. API·worker 계정을 별도로 사용하면 생성된 AI 테이블의 SELECT·INSERT·UPDATE·DELETE 및 `flyway_schema_history`의 SELECT 권한을 부여합니다.

**Flyway Job 성공 → 같은 커밋의 API·worker 배포** 순서로 적용합니다. Job 실패 시 배포를 진행하지 않습니다. 재실행은 이미 적용된 V1·V2를 다시 실행하지 않습니다. 실제 DB 접속값은 인프라팀이 Secret으로 주입합니다.
