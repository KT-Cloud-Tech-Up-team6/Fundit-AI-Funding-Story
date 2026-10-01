# Funding Story AI — 인프라팀 전달용 환경변수

대상: dev EKS의 AI API와 worker 3종. 아래 인증값은 `dev / funding-story-ai` 전용입니다. prod는 인증 주체와 신뢰 설정을 별도로 확정합니다. 비밀번호·토큰 원문은 Notion이나 Git에 적지 않습니다.

## 실행

| 환경변수 | 값 |
| --- | --- |
| `APP_ENV` | `dev` |
| `MODEL_PROFILE` | `runtime` |

### 작업 시간 제한 (기본값 사용 가능)

| 환경변수 | 기본값 |
| --- | --- |
| `FUNDING_STORY_JOB_TIMEOUT_SECONDS` | `1200` (접수부터 20분) |
| `COMPLETION_DELIVERY_TIMEOUT_SECONDS` | `1500` (접수부터 25분) |
| `IMAGE_GENERATION_BUDGET_SECONDS` | `900` (이미지 생성 단계 15분) |

작업 제한 < callback 전달 제한 < BE callback 대기 제한 순으로 맞춥니다.
BE의 `FUNDING_STORY_AI_RUN_CALLBACK_TIMEOUT_MINUTES` 기본값은 30분입니다.
Page Summary 이미지 서명 URL 유효기간 60분과는 별도 값입니다.

## PostgreSQL DB

BE와 같은 PostgreSQL 서버의 AI용 논리 DB를 사용합니다. `DB_NAME`은 [마이그레이션 Job](funding-story-ai-migration.md)과 동일하게 지정합니다.

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

## Google Vertex AI (WIF · ADC로 설정 로드)

ADC는 인증 설정을 자동으로 찾는 방법이고, WIF는 EKS 토큰으로 Google 단기 토큰을 받는 방식입니다. Google SDK는 아래 JSON을 ADC로 찾아 WIF 인증에 사용합니다. `external_account` JSON에는 비밀키가 없습니다.

| 환경변수 | 값 |
| --- | --- |
| `GOOGLE_CLOUD_PROJECT` | `project-c0d4f6ae-c737-445d-93b` |
| `GOOGLE_CLOUD_LOCATION` | `global` (기본값) |
| `GOOGLE_APPLICATION_CREDENTIALS` | `/var/run/secrets/google-wif/credentials.json` |

Google 설정 파일은 [google-wif-dev.json](../deploy/auth/google-wif-dev.json)을 사용합니다. Google 토큰의 audience와 마운트 경로는 [인증 계약](aws-eks-provider-auth.md#google-dev-설정값)을 따릅니다.

## OpenAI 이미지 생성 (WIF)

| 환경변수 | 값 |
| --- | --- |
| `OPENAI_IDENTITY_PROVIDER_ID` | `idp_65c3c65ddba506b5f620bfc7` |
| `OPENAI_SERVICE_ACCOUNT_ID` | `user-1aa935e64b36d873abba6933` |
| `OPENAI_WIF_AUDIENCE` | `https://api.openai.com/v1` |
| `OPENAI_WIF_TOKEN_FILE` | `/var/run/secrets/openai-wif/token` |

WIF 항목의 ID와 경로는 비밀값이 아닙니다. dev의 Google·OpenAI 신뢰 및 호출 권한 설정은 완료했습니다. 인프라팀은 Google 설정 JSON과 각 플랫폼 전용 EKS 토큰을 별도 마운트합니다. 실제 EKS 토큰 교환·모델 호출은 아직 미검증입니다. 배포에는 `OPENAI_API_KEY`나 Google 서비스 계정 private key를 주입하지 않습니다.
