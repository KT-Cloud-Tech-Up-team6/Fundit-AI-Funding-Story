# Funding Story AI — 인프라팀 전달용 환경변수

대상: EKS의 AI API와 worker 3종. dev/prod별로 같은 설정을 주입합니다. `<...>`는 실제 배포값으로 교체하며, 비밀번호·토큰 원문은 Notion이나 Git에 적지 않습니다.

## 실행

- `APP_ENV` — `dev` 또는 `prod`
- `MODEL_PROFILE` — `runtime`

## AI PostgreSQL

- `DB_HOST` — `<AI_DB_HOST>`
- `DB_PORT` — `<AI_DB_PORT>`
- `DB_NAME` — `<AI_DB_NAME>`
- `DB_USERNAME` — `<AI_DB_USERNAME>` (Secret 참조)
- `DB_PASSWORD` — `<AI_DB_PASSWORD>` (Secret 참조)
- `DB_SSLMODE` — DB에서 TLS 모드를 지정한 경우에만 설정

## BE 연결에 사용하는 AI 서비스 설정

- `AI_SERVICE_TOKEN` — `<BE와 공유한 토큰>` (Secret 참조; BE의 `FUNDING_STORY_AI_TOKEN`과 동일)
- `PROJECT_SERVICE_BASE_URL` — `<BE 내부 URL>`
- `INTERNAL_API_KEY` — `<BE가 검증하는 내부 키>` (Secret 참조)

## Google Vertex AI (ADC)

- `GOOGLE_CLOUD_PROJECT` — `<GCP_PROJECT_ID>`
- `GOOGLE_CLOUD_LOCATION` — `global` (다른 위치를 사용하면 실제 값으로 변경)
- `GOOGLE_APPLICATION_CREDENTIALS` — Google `external_account` JSON의 Pod 내 경로. 예: `/var/run/secrets/google-wif/credentials.json`

## OpenAI 이미지 생성 (WIF)

- `OPENAI_IDENTITY_PROVIDER_ID` — `<OPENAI_WIF_PROVIDER_ID>`
- `OPENAI_SERVICE_ACCOUNT_ID` — `<OPENAI_SERVICE_ACCOUNT_ID>`
- `OPENAI_WIF_AUDIENCE` — OpenAI provider와 토큰에 설정한 audience. 예: `https://api.openai.com/v1`
- `OPENAI_WIF_TOKEN_FILE` — OpenAI projected token의 Pod 내 경로. 예: `/var/run/secrets/openai-wif/token`

ADC·WIF 항목의 ID와 경로는 인증 정보 자체가 아닙니다. 해당 파일 마운트와 Google·OpenAI 측 신뢰 설정은 별도로 준비되어야 합니다. 운영에는 `OPENAI_API_KEY`나 Google 서비스 계정 private key를 주입하지 않습니다.
