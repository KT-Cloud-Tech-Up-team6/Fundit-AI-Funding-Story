# Fundit AI Funding Story

제품 정보·이미지·대화를 바탕으로 제품의 핵심 강점을 정리하고, 확인된 내용을 디자인 템플릿의 텍스트·이미지 슬롯에 채우는 AI 서비스입니다. 생성 중에는 임시 scene을 사용하고, 최종 API 결과는 블록별 PNG 자산 매니페스트와 편집 가능한 프로젝트 정보 텍스트입니다.

## 구성

- Python 3.12.14 / uv / FastAPI
- LangGraph 1.2.11: 블록 선택 → 문구 생성 → 슬롯별 이미지 생성 → 임시 디자인 조립. PostgreSQL checkpoint로 이어서 실행
- Celery + Redis: 비동기 실행과 전달. Redis에는 문서나 최종 결과를 저장하지 않음
- PostgreSQL: 입력 세션·확인 버전·실행 상태·자산 메타데이터·체크포인트
- Google GenAI 공식 SDK / LangSmith 선택적 추적
- Pillow 기반 서버 PNG export. Pretendard 1.3.9 파일을 checksum으로 고정한 Docker 이미지
- FE·BE 저장소는 호출 계약 확인에만 사용하며 이번 범위에서는 수정하지 않음

## 로컬 실행

```sh
uv sync --frozen
cp .env.example .env
# .env의 Google Cloud 프로젝트와 ADC, 서비스 토큰을 설정
# 실제 모델 호출은 비용이 발생합니다.
docker compose up -d
uv run python -m funding_story.store
uv run uvicorn funding_story.api:app --host 127.0.0.1 --port 58001
```

별도 터미널에서 워커와 미전달 작업 복구 스케줄러를 실행합니다.

```sh
uv run celery -A funding_story.tasks worker --pool=solo --loglevel=INFO
uv run celery -A funding_story.tasks beat --loglevel=INFO --schedule=data/celerybeat-schedule
```

API 실행 전에 DB 초기화 명령을 한 번 실행하세요. 워커는 API와 동일한 환경 변수·DB·파일 저장소를 사용해야 합니다. `--pool=solo`는 macOS 내부 검증용입니다. 운영 워커 수·리소스·보존 기간은 인프라 협의 대상입니다.

```sh
uv run pytest -q
uv run ruff check src tests
```

테스트는 워커 DB와 분리된 `funding_ai_test` PostgreSQL을 사용합니다. 새 Compose 볼륨은 이를 자동 생성합니다. 기존 볼륨은 테스트 DB를 별도로 생성하거나 `TEST_DATABASE_URL`로 전용 테스트 DB를 지정하세요. 모델 호출은 테스트에서 대체하며, 테스트 프로젝트 작업은 자동 실행 대상에서 제외합니다. 실제 모델 검증은 별도 기록합니다.

## 계약과 책임

- FE → BE → AI. AI는 내부 Bearer 토큰과 프로젝트 범위를 요구하며, BE가 사용자·프로젝트 소유권을 확인합니다.
- 사용자 확인 전 생성할 수 없습니다. 수정 후에는 새 입력 버전을 확인해야 합니다.
- 동일 메시지 ID / 생성 idempotency key는 중복 작업을 만들지 않습니다.
- 공급자 429·5xx는 15초/30초 대기 후 재시도합니다. JSON 계약 오류는 LangGraph가 오류 내용을 전달해 최대 두 번 추가 생성합니다.
- LLM 결과의 사실·표현 품질을 판정하는 추가 모델 호출이나 자동 교정은 없습니다.
- 실패한 이미지 슬롯은 명시적으로 부분 완료 상태에 남습니다. 재시도는 성공한 이미지를 재사용합니다.
- 후보 scene은 AI가 임시 보관합니다. 문구 수정값을 `/v1/runs/{id}/exports`에 전달하면 블록별 PNG와 하단 텍스트를 반환합니다.
- BE가 최종 본문을 저장한 뒤 `/v1/exports/{id}/commit`으로 저장 revision을 확인해야 임시 scene·입력 snapshot·실행 checkpoint를 정리합니다.
- 저장된 PNG의 디자인 재편집과 부분 블록 재생성 API는 제공하지 않습니다. 하단 일반 텍스트 편집은 FE/BE 본문 책임입니다.
- 정상가를 입력하지 않으면 만들지 않습니다. 선물 카드는 3종 디자인을 유지하고 미입력 카드는 사용자가 편집합니다.

## 저장소 안내

`src/funding_story/resources/template.json`은 검토한 생활가전 템플릿을 이식한 것입니다. 생성 시 위치·크기·폰트 등 디자인 요소를 임의 변경하지 않습니다. `tests/fixtures`는 실제 판매 정보가 아닌 내부 검증 자료입니다.

운영 S3 버킷·권한, Gateway 인증 연결, LangSmith API 키, 게시 정책은 배포 전에 관련 팀과 연결·검증해야 합니다. 로컬 개발용 토큰·DB 암호를 운영에 사용하지 마세요. 로컬 PNG export는 `PRETENDARD_FONT_PATH`에 Pretendard variable TTF의 절대 경로가 필요하며 Docker 이미지는 고정 버전을 설치합니다.

Dockerfile은 API 이미지용입니다. 워커는 같은 이미지에서 `/app/.venv/bin/celery -A funding_story.tasks worker`로 실행합니다. DB 초기화는 별도 초기화 작업으로 실행하고, 여러 API 인스턴스가 동시에 DDL을 수행하지 않도록 합니다. 운영 포트 공개·인증·파일 볼륨·S3 설정은 이 Dockerfile만으로 완성되지 않습니다. CI 설정은 로컬에서 작성했으며 GitHub에서 실행하지 않았습니다.

API와 팀별 연결 항목은 [실행 계약](docs/architecture.md), 기존 FE 화면과의 대응은 [프론트 호출 계약](docs/frontend-call-contract.md), 실제 검증 범위는 [검증 기록](docs/validation.md)에서 확인합니다.
