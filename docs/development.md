# 개발 환경과 실행

저장소 루트에서 실행한다. Python 버전은 `.python-version`, 의존성은 `uv.lock`을 따른다. FE/BE 저장소나 Node.js 설치 없이 AI API를 실행할 수 있다.

## 설치와 설정

```sh
uv sync --frozen
cp .env.example .env
uv run playwright install chromium
uv run python scripts/install_font.py
docker compose up -d
uv run python -m funding_story.store
```

Linux에서는 Chromium 설치에 `--with-deps`를 추가한다. 폰트는 Pretendard 1.3.9의 SHA-256을 검사한 뒤 `data/fonts/PretendardVariable.ttf`에 저장한다. `PRETENDARD_FONT_PATH`가 지정되어 있으면 그 경로를 사용한다. 다른 로컬 저장소의 폰트에 의존하지 않는다.

| 설정 | 용도 |
|---|---|
| `DATABASE_URL` | PostgreSQL 세션·실행·자산 메타데이터·체크포인트 |
| `CELERY_BROKER_URL` | 작업 전달용 Redis |
| `AI_SERVICE_TOKEN` | BE → AI 내부 인증, 운영 비밀 관리 필요 |
| `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` | Google 모델 프로젝트·위치 |
| `TEXT_MODEL`, `IMAGE_MODEL` | 텍스트·이미지 모델 |
| `STORAGE_BACKEND`, `STORAGE_DIR` | local 또는 s3; 로컬 자산 경로 |
| `S3_BUCKET`, `S3_ENDPOINT` | S3 자산 설정, 자격증명은 SDK 기본 공급 체인 |
| `PRETENDARD_FONT_PATH` | 별도 폰트 경로가 필요할 때 지정 |
| `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | 선택적 추적 |

Google SDK는 ADC를 사용한다. 로컬 최초 인증은 `gcloud auth application-default login`으로 설정하며 인증 파일을 저장소에 복사하지 않는다. LangSmith로 전송할 정보·보존 범위는 배포 전 인프라/보안팀과 정한다.

## 프로세스

API, worker, beat를 각각 실행한다. 세 프로세스가 동일한 `.env`와 DB·스토리지를 사용해야 한다.

```sh
uv run uvicorn funding_story.api:app --host 127.0.0.1 --port 58001
uv run celery -A funding_story.tasks worker --pool=solo --loglevel=INFO
uv run celery -A funding_story.tasks beat --loglevel=INFO --schedule=data/celerybeat-schedule
```

`GET /health`는 상태 확인용이며 `/v1` 요청에는 내부 인증과 프로젝트 ID가 필요하다. OpenAPI UI는 `/docs`다. `solo`는 macOS 로컬용이며 운영 동시성은 별도로 정한다. 스케줄러는 중복 기동하지 않는다.

## 테스트와 패키징

```sh
uv run ruff check src tests scripts
uv run pytest -q
uv build
```

Compose 초기화는 개발 DB와 테스트 DB를 만든다. 기존 볼륨에 테스트 DB가 없으면 별도로 만들거나 `TEST_DATABASE_URL`로 전용 DB를 지정한다. 기본 테스트 DB는 `funding_ai_test`이며 실데이터 DB를 테스트에 지정하지 않는다. 테스트의 모델 호출은 mock이다. 렌더러는 실제 Chromium/폰트를 사용한다.

## Docker

```sh
docker build -t fundit-ai-funding-story:local .
```

Dockerfile에 uv, 의존성, 검증된 폰트, Chromium을 포함한다. 기본 명령은 8000 포트의 API다. 동일 이미지의 워커 명령은 `/app/.venv/bin/celery -A funding_story.tasks worker --loglevel=INFO`이다. DB 초기화·beat는 별도 프로세스로 운영한다.

컨테이너에서는 localhost가 Compose DB를 가리키지 않는다. 네트워크에 맞춰 `DATABASE_URL`, `CELERY_BROKER_URL`, ADC, 저장 볼륨 또는 S3를 주입한다. 운영 토큰·네트워크·CDN·자산 보존 기간은 이 Dockerfile만으로 완성되지 않는다. 현재 Docker build와 원격 CI의 실제 실행 여부는 [검증 기록](validation.md)을 따른다.
