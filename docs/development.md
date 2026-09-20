# 개발 환경과 실행

저장소 루트에서 실행한다. Python 버전은 `.python-version`, 의존성은 `uv.lock`을 따른다. FE/BE 저장소나 Node.js 설치 없이 AI API를 실행할 수 있다.

## 설치와 설정

```sh
uv sync --frozen
cp .env.example .env
uv run playwright install chromium
uv run python scripts/install_font.py
docker compose up -d
docker compose run --rm migrate validate
```

Linux에서는 Chromium 설치에 `--with-deps`를 추가한다. 폰트는 Pretendard 1.3.9의 SHA-256을 검사한 뒤 `data/fonts/PretendardVariable.ttf`에 저장한다. `PRETENDARD_FONT_PATH`가 지정되어 있으면 그 경로를 사용한다. 다른 로컬 저장소의 폰트에 의존하지 않는다. 로컬 PostgreSQL은 다른 서비스와 겹치지 않는 호스트 포트 `5440`을 사용한다.

| 설정 | 용도 |
|---|---|
| `APP_ENV` | `local`, `test`, `dev`, `prod`; dev/prod에서 로컬 DB 기본값 거부 |
| `DB_HOST`, `DB_PORT`, `DB_NAME` | PostgreSQL 주소와 AI 전용 DB 이름 |
| `DB_USERNAME`, `DB_PASSWORD`, `DB_SSLMODE` | PostgreSQL 인증과 선택적 TLS 모드 |
| `DB_POOL_MIN_SIZE`, `DB_POOL_MAX_SIZE`, `DB_POOL_TIMEOUT_SECONDS` | API/worker runtime pool 제한 |
| `DB_CHECKPOINT_POOL_MAX_SIZE` | 기존 DB 호환 설정; Funding Story runtime에서는 사용하지 않음 |
| `DATABASE_URL` | 한 배포 주기 동안만 유지하는 호환 override; 신규 설정에는 사용하지 않음 |
| `CELERY_BROKER_URL` | 작업 전달용 Redis |
| `FUNDING_STORY_STATE_REDIS_URL`, `FUNDING_STORY_STATE_TTL_SECONDS` | Funding Story 단기 세션·작업 상태 |
| `CONTENT_INSIGHTS_PAGE_SUMMARY_QUEUE` | 필수 페이지 요약 전용 Celery queue |
| `CONTENT_INSIGHTS_STORYLINE_QUEUE` | 스토리라인 전용 Celery queue |
| `AI_SERVICE_TOKEN` | BE → AI 내부 인증, 운영 비밀 관리 필요 |
| `PROJECT_SERVICE_BASE_URL`, `INTERNAL_API_KEY` | AI → BE 내부 callback·업로드 대상 발급 |
| `INTERNAL_HTTP_TIMEOUT_SECONDS`, `COMPLETION_CALLBACK_ATTEMPTS` | AI → BE 요청 timeout·완료 callback 재시도 |
| `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` | Google 모델 프로젝트·위치 |
| `TEXT_MODEL`, `IMAGE_MODEL` | 텍스트·이미지 모델 |
| `PRETENDARD_FONT_PATH` | 별도 폰트 경로가 필요할 때 지정 |

Google SDK는 ADC를 사용한다. 로컬 최초 인증은 `gcloud auth application-default login`으로 설정하며 인증 파일을 저장소에 복사하지 않는다. Funding Story 프롬프트·입출력은 외부 tracing으로 전송하지 않는다.

## DB migration

Flyway는 Content Insights가 사용하는 기존 PostgreSQL schema를 관리한다. 기존 V2 checkpoint schema는 migration 이력 호환을 위해 유지하지만 Funding Story runtime은 읽거나 쓰지 않는다. `db/migration/V*__*.sql`은 적용 후 수정하지 않는다. API와 worker는 Content Insights DB와 Funding Story Redis 준비 상태를 확인하며 runtime은 migration을 직접 실행하지 않는다.

```sh
# Compose DB에 적용 및 확인
docker compose run --rm migrate
docker compose run --rm migrate validate

# .env의 DB_* 설정을 대상으로 Docker 기반 Flyway 실행
uv run python scripts/migrate.py info
uv run python scripts/migrate.py migrate
uv run python scripts/migrate.py validate
```

Flyway 도입 전부터 존재하던 DB는 먼저 백업한 뒤 구조 검사부터 수행한다. 기본 실행은 읽기 전용 검사이며, 구조가 정확히 일치할 때만 명시적으로 baseline한다.

```sh
uv run python scripts/baseline_existing_db.py
uv run python scripts/baseline_existing_db.py --apply
```

기존 Compose volume에는 과거 초기화 스크립트가 만든 `funding_be`나 `funding_ai_test`가 남아 있을 수 있다. 코드 변경만으로 기존 DB가 삭제되지는 않는다. 보존할 데이터가 없음을 확인하고 백업한 경우에만 Compose를 내린 뒤 해당 프로젝트의 `*_ai-db` volume을 명시적으로 제거한다. 자동화나 일반 시작 명령에서 volume을 삭제하지 않는다.

## 프로세스

API, worker, beat를 각각 실행한다. 모든 프로세스가 동일한 `.env`, Redis, Content Insights DB 설정을 사용해야 한다. Content Insights의 필수 작업이 이미지 생성 부하에 밀리지 않도록 queue subscription을 분리한다.

```sh
uv run uvicorn funding_story.api:app --host 127.0.0.1 --port 58001
uv run celery -A funding_story.tasks worker --pool=solo --loglevel=INFO -Q celery
uv run celery -A funding_story.tasks worker --pool=solo --loglevel=INFO -Q content-insights.page-summary
uv run celery -A funding_story.tasks worker --pool=solo --loglevel=INFO -Q content-insights.storyline
uv run celery -A funding_story.tasks beat --loglevel=INFO --schedule=data/celerybeat-schedule
```

`GET /health`는 상태 확인용이며 `/api/v1/ai` 요청에는 내부 인증과 프로젝트 ID가 필요하다. OpenAPI UI는 `/docs`다. `solo`는 macOS 로컬용이며 운영 동시성은 별도로 정한다. 스케줄러는 중복 기동하지 않는다. 단일 로컬 worker가 필요하면 `-Q celery,content-insights.page-summary,content-insights.storyline`을 사용할 수 있지만 운영 격리 구성이 아니다. Content Insights smoke test와 복구 절차는 [운영·연동 안내](content-insights-operations.md)를 따른다.

## 테스트와 패키징

```sh
uv run ruff check src tests scripts
uv run pytest -q
uv build
```

테스트는 쉘의 `DATABASE_URL`과 `TEST_DATABASE_URL`을 사용하지 않는다. DB 통합 테스트가 요청할 때만 Testcontainers가 임의 포트의 PostgreSQL 17 컨테이너를 만들고 운영과 같은 Flyway migration을 적용한 뒤 종료 시 제거한다. application fake와 계층 의존성 단위 테스트는 Docker/DB 없이 실행할 수 있다. DB 통합 테스트를 포함한 전체 suite에서는 Docker가 필요하며 사용할 수 없으면 skip하지 않고 실패한다. 모델 호출은 mock이고 렌더러는 실제 Chromium/폰트를 사용한다.

## Docker

```sh
docker build -t fundit-ai-funding-story:local .
```

Dockerfile에 uv, 의존성, 검증된 폰트, Chromium을 포함한다. 기본 명령은 8000 포트의 API다. 동일 이미지의 워커 명령은 `/app/.venv/bin/celery -A funding_story.tasks worker --loglevel=INFO`이다. DB 초기화·beat는 별도 프로세스로 운영한다.

컨테이너에서는 localhost가 Compose 의존성을 가리키지 않는다. 네트워크에 맞춰 `DB_*`, 두 Redis URL, `PROJECT_SERVICE_BASE_URL`, 내부 토큰, ADC를 주입한다. 최종 객체 저장소는 BE가 발급하는 presigned URL로만 접근하므로 AI 저장소 설정은 없다.

## CNPG 배포 계약

dev/prod에서는 `APP_ENV`, `DB_HOST`, `DB_PORT`, `DB_NAME`, pool 크기를 ConfigMap으로, `DB_USERNAME`, `DB_PASSWORD`, `AI_SERVICE_TOKEN`을 Secret으로 API·worker·beat·migration Job에 동일하게 주입한다. 권장 순서는 DB 복구 지점 확인 → Flyway Job → `validate` → API/worker rollout → `/health/ready` → BE → AI smoke test다. migration 실패 시 rollout을 중단한다.

연결 예산은 다음 상한을 사용한다.

```text
API DB_POOL_MAX_SIZE × API replica
+ worker DB_POOL_MAX_SIZE × Content Insights worker process
+ migration/운영 여유분
<= CNPG 허용 연결 수
```

실제 replica 수와 CNPG 한도는 인프라팀 확정값으로 배포 전에 채운다. migration 계정의 DDL 권한과 runtime 계정의 DML 권한 분리도 CNPG Secret 설계 시 결정한다.
