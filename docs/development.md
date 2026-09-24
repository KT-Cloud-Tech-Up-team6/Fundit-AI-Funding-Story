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

### 모델 프로필

`MODEL_PROFILE` 하나로 텍스트·이미지 Provider·모델·OpenAI 인증 방식을 선택한다. 아래 조합에 어긋나는 개별 override는 시작 단계에서 거부한다.

| 프로필 | 용도 | 텍스트 | 이미지 | 인증 |
|---|---|---|---|---|
| `runtime` | dev/prod EKS·운영동등 통합 검증 | `gemini-3.8-flash` | `gpt-image-2.5-flare` | Google external ADC + OpenAI EKS WIF |
| `local_openai_smoke` | 로컬 운영 모델 smoke | `gemini-3.8-flash` | `gpt-image-2.5-flare` | `gcloud` ADC + OpenAI API key |
| `local_google_experiment` | 기본 로컬 프롬프트·디자인 실험 | `gemini-3.8-flash` | 기본 `gemini-3.1-flash-image`; Gemini 모델 override 허용 | `gcloud` ADC |
| 자동 테스트 | `APP_ENV=test`, 외부 호출 차단 | mock | mock | 없음 |

`.env.example`은 `local_google_experiment`이다. OpenAI 로컬 smoke는 `MODEL_PROFILE=local_openai_smoke`와 `OPENAI_API_KEY`를 함께 설정한다. dev/prod에서는 `MODEL_PROFILE=runtime`만 허용한다.

| 필수 설정 | 용도 |
|---|---|
| `APP_ENV` | `local`, `test`, `dev`, `prod`; dev/prod에서 로컬 DB 기본값 거부 |
| `MODEL_PROFILE` | 모델·Provider·인증 조합; 개별 값을 독립적으로 선택하지 않음 |
| `AI_SERVICE_TOKEN` | BE → AI 내부 인증, 운영 비밀 관리 필요 |
| `INTERNAL_API_KEY` | AI → BE callback·업로드 대상 발급 인증 |
| `GOOGLE_CLOUD_PROJECT` | Gemini 텍스트와 선택적 Google 이미지 프로젝트 |
| `OPENAI_API_KEY` | `local_openai_smoke`에서만 필요; 배포 금지 |

dev/prod 배포에서는 다음 값을 ConfigMap·Secret으로 주입한다.

| 배포 설정 | 용도 |
|---|---|
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USERNAME`, `DB_PASSWORD` | 기존 AI PostgreSQL 접속 |
| `GOOGLE_APPLICATION_CREDENTIALS` | EKS에서 마운트한 GCP `external_account` ADC JSON 경로; local은 `gcloud` ADC 사용 |
| `OPENAI_IDENTITY_PROVIDER_ID`, `OPENAI_SERVICE_ACCOUNT_ID`, `OPENAI_WIF_AUDIENCE` | OpenAI Platform이 발급한 WIF provider ID·OpenAI project service account ID와 EKS token audience; dev/prod 필수 |
| `OPENAI_WIF_TOKEN_FILE` | EKS projected ServiceAccount token 경로; dev/prod 필수 |
| `PROJECT_SERVICE_BASE_URL`, `INTERNAL_API_KEY` | AI → BE 내부 연동 |

텍스트와 선택적 Google 이미지 Provider는 ADC를 사용한다. 로컬 최초 인증은 `gcloud auth application-default login`으로 설정한다. AWS EKS에서는 GCP external-account credential configuration을 `GOOGLE_APPLICATION_CREDENTIALS`로 마운트해 같은 ADC 경로를 사용한다. OpenAI 이미지 Provider의 dev/prod 인증은 EKS projected ServiceAccount token 기반 WIF만 허용한다. 두 Provider는 자격 증명 파일·토큰 경로를 공유하지 않는다. GCP 서비스 계정 JSON key와 OpenAI API key는 배포하지 않는다. Funding Story 프롬프트·입출력은 외부 tracing으로 전송하지 않는다. 배포 계약과 검증 절차는 [EKS Provider 인증](aws-eks-provider-auth.md)을 따른다.

같은 Provider 계정·위치·모델의 worker는 PostgreSQL 제어 row와 transaction lock으로 호출 제한을 공유한다. 429는 신규 호출 대기와 동시성 축소를 적용하고 정상 호출 3회마다 복구한다. 입력·생성 결과는 제어 row에 저장하지 않는다.

## DB migration

Flyway는 AI PostgreSQL schema를 관리한다. 기존 V2 checkpoint schema는 migration 이력 호환을 위해 유지하지만 Funding Story runtime은 읽거나 쓰지 않는다. `db/migration/V*__*.sql`은 적용 후 수정하지 않는다. API와 worker는 같은 DB readiness를 확인하며 runtime은 migration을 실행하지 않는다.

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

로컬은 단일 polling worker를 사용할 수 있다. 운영은 lane별 Deployment로 분리한다.

```sh
uv run uvicorn funding_story.api:app --host 127.0.0.1 --port 58001
uv run python -m funding_story.worker --lane all

# 운영 lane
uv run python -m funding_story.worker --lane funding-story
uv run python -m funding_story.worker --lane page-summary
uv run python -m funding_story.worker --lane storyline
```

`GET /health`는 상태 확인용이며 `/api/v1/ai` 요청에는 내부 인증과 프로젝트 ID가 필요하다. OpenAPI UI는 `/docs`다. polling 기본 간격은 0.5초이며 `--concurrency`로 lane별 동시성을 지정한다. Content Insights smoke test와 복구 절차는 [운영·연동 안내](content-insights-operations.md)를 따른다.

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

Dockerfile에 uv, 의존성, 검증된 폰트, Chromium을 포함한다. 기본 명령은 8000 포트의 API다. 동일 이미지의 worker 명령은 `/app/.venv/bin/python -m funding_story.worker --lane <lane>`이다. DB migration은 별도 Job으로 실행한다.

컨테이너에서는 localhost가 PostgreSQL을 가리키지 않는다. 네트워크에 맞춰 `DB_*`, `PROJECT_SERVICE_BASE_URL`, 내부 토큰, Provider 인증을 주입한다. 최종 객체 저장소는 BE가 발급하는 presigned URL로만 접근하므로 AI 저장소 설정은 없다.

## GitHub Actions 이미지 게시

PR에서는 기존 테스트가 통과한 뒤 `linux/amd64` Docker 이미지를 빌드한다. AWS 인증은 수행하지 않는다. `main` push에서는 테스트 통과 후 GitHub OIDC로 임시 인증해 AI 전용 ECR에 `sha-<commit SHA>` 태그로 이미지를 올린다.

| 게시 설정 | 인프라팀 확정값 |
|---|---|
| push 브랜치 | `main` |
| AWS 리전 | `ap-northeast-2` |
| ECR 리포지토리 | `899957568205.dkr.ecr.ap-northeast-2.amazonaws.com/fundit-ai-funding-story` |
| IAM Role | `arn:aws:iam::899957568205:role/fundit-ai-funding-story-ci-role` |
| 이미지 태그 | `sha-<commit SHA>` |

게시 워크플로에는 `environment:`와 AWS Access Key를 설정하지 않는다. API·worker의 배포 환경 변수와 Secret은 위 [설치와 설정](#설치와-설정) 및 [EKS Provider 인증 계약](aws-eks-provider-auth.md)을 따른다. DB migration과 실제 EKS 배포는 별도 Job·GitOps에서 진행한다.

인프라팀에 전달할 환경변수와 런타임 인증 요건은 [AWS 배포 설정 인계](aws-infra-handoff.md)에 정리했다.

## PostgreSQL 운영 전환 계약

| 환경 | PostgreSQL |
|---|---|
| local/test | Compose 또는 Testcontainers의 임시 PostgreSQL 17; 운영에 배포하지 않음 |
| dev/prod | 기존 AWS EKS CNPG의 Content Insights용 AI 논리 DB; Project Service DB와 분리 |

| 전환 대상 | PostgreSQL 방식 |
|---|---|
| 세션·채팅·run | 기존 `ai_records`의 TTL row |
| 멱등성 | 기존 `ai_requests` |
| 작업 전달·복구 | `ai_records` 상태 + DB polling + row lock·lease |
| 이미지 호출 제어 | `ai_records` 제어 row + transaction lock |

만료 row는 TTL 기준으로 정리한다. 새 테이블·ERD나 AI 전용 PostgreSQL 서버·컨테이너를 추가하지 않는다. AI는 Flyway·schema·query·polling worker를, 인프라·GitOps는 CNPG·Secret·백업·연결 한도를 소유한다.

dev/prod에서는 `APP_ENV`, `DB_HOST`, `DB_PORT`, `DB_NAME`, pool 크기를 ConfigMap으로, `DB_USERNAME`, `DB_PASSWORD`, `AI_SERVICE_TOKEN`을 Secret으로 API·polling worker·migration Job에 동일하게 주입한다. 순서는 DB 복구 지점 확인 → Flyway Job → `validate` → API·polling worker rollout → `/health/ready` → BE → AI smoke test다. migration 실패 시 rollout을 중단한다.

연결 예산은 다음 상한을 사용한다.

```text
API DB_POOL_MAX_SIZE × API replica
+ worker DB_POOL_MAX_SIZE × Content Insights worker process
+ migration/운영 여유분
<= CNPG 허용 연결 수
```

실제 replica 수와 CNPG 한도는 인프라팀 확정값으로 배포 전에 채운다. migration 계정의 DDL 권한과 runtime 계정의 DML 권한 분리도 CNPG Secret 설계 시 결정한다.
