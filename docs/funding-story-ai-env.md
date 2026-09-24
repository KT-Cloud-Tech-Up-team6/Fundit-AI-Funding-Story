# Funding Story AI — 인프라팀 전달용 환경변수

대상: EKS의 AI API와 worker 3종. dev/prod에 동일한 변수 목록을 주입합니다. `<...>`는 환경별 실제 값으로 교체합니다. 비밀번호·토큰 원문은 Notion이나 Git에 적지 않습니다.

## 실행

| 환경변수 | 값 |
| --- | --- |
| `APP_ENV` | `dev` 또는 `prod` |
| `MODEL_PROFILE` | `runtime` |

## PostgreSQL (백엔드와 공유하는 DB)

| 환경변수 | 값 |
| --- | --- |
| `DB_HOST` | `<DB_HOST>` |
| `DB_PORT` | `<DB_PORT>` |
| `DB_NAME` | `<DB_NAME>` |
| `DB_USERNAME` | `<DB_USERNAME>` (Secret 참조) |
| `DB_PASSWORD` | `<DB_PASSWORD>` (Secret 참조) |
| `DB_SSLMODE` | DB의 TLS 모드 (필요한 경우만) |

## BE 연결에 사용하는 AI 서비스 설정

| 환경변수 | 값 |
| --- | --- |
| `AI_SERVICE_TOKEN` | `<BE와 공유한 토큰>` (Secret 참조) |
| `PROJECT_SERVICE_BASE_URL` | `<BE 내부 URL>` |
| `INTERNAL_API_KEY` | `<BE가 검증하는 내부 키>` (Secret 참조) |

`AI_SERVICE_TOKEN`은 BE의 `FUNDING_STORY_AI_TOKEN`과 같은 값입니다.

## Google Vertex AI (ADC)

| 환경변수 | 값 |
| --- | --- |
| `GOOGLE_CLOUD_PROJECT` | `<GCP_PROJECT_ID>` |
| `GOOGLE_CLOUD_LOCATION` | `global` (기본값) |
| `GOOGLE_APPLICATION_CREDENTIALS` | Pod 내 Google `external_account` JSON 경로 (예: `/var/run/secrets/google-wif/credentials.json`) |

## OpenAI 이미지 생성 (WIF)

| 환경변수 | 값 |
| --- | --- |
| `OPENAI_IDENTITY_PROVIDER_ID` | `<OPENAI_WIF_PROVIDER_ID>` |
| `OPENAI_SERVICE_ACCOUNT_ID` | `<OPENAI_SERVICE_ACCOUNT_ID>` |
| `OPENAI_WIF_AUDIENCE` | Provider·토큰에 설정한 audience (예: `https://api.openai.com/v1`) |
| `OPENAI_WIF_TOKEN_FILE` | Pod 내 OpenAI projected token 경로 (예: `/var/run/secrets/openai-wif/token`) |

ADC·WIF 항목의 ID와 경로는 인증 정보 자체가 아닙니다. 해당 파일 마운트와 Google·OpenAI 측 신뢰 설정은 별도로 준비되어야 합니다. 운영에는 `OPENAI_API_KEY`나 Google 서비스 계정 private key를 주입하지 않습니다.
