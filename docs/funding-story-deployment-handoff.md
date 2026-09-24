# Funding Story AI — AI·BE·Infra 배포 연동 인계

이 문서는 AI·BE·Infra/GitOps가 함께 확인할 **서비스 연동·환경변수·외부 인증 계약**이다. dev/prod의 실제 값과 Secret은 인프라·GitOps 설정에서 관리하고, 비밀번호·토큰 원문은 Notion이나 Git에 적지 않는다.

**이 문서만으로 서비스가 바로 동작하지는 않는다.** 아래 변수의 실제 값뿐 아니라 EKS 토큰 마운트, Google·OpenAI 측 WIF 신뢰·권한 설정, DB·BE 연결이 준비되어야 한다. 설정이 완료되면 SDK가 EKS 토큰으로 단기 접근 토큰을 받아 사용하므로 운영용 Google private key나 OpenAI API key는 필요하지 않다. `/health/ready`는 DB 준비 상태를 확인하며 외부 모델 호출 권한까지 확인하지는 않는다.

## 서비스 정보

- **저장소:** [Fundit-AI-Funding-Story](https://github.com/KT-Cloud-Tech-Up-team6/Fundit-AI-Funding-Story)
- **이미지:** `899957568205.dkr.ecr.ap-northeast-2.amazonaws.com/fundit-ai-funding-story:sha-<commit SHA>`
- **이미지 게시:** `main` push 시 GitHub OIDC로 ECR에 업로드하도록 설정 완료. EKS 배포는 별도 GitOps 설정이 필요하다.
- **운영 모델:** `MODEL_PROFILE=runtime`에서 텍스트는 Vertex AI `gemini-3.8-flash`, 이미지는 OpenAI `gpt-image-2.5-flare`를 사용한다. 각 대상 project의 모델 호출 권한이 필요하다.
- **API:** 이미지 기본 명령, 포트 `8000`. 상태 확인 경로는 `/health`, `/health/ready`.
- **worker:** 같은 이미지에서 아래 명령을 실행한다. `<lane>`은 `funding-story`, `page-summary`, `storyline` 중 하나이며 각각 별도 프로세스가 필요하다.

```sh
/app/.venv/bin/python -m funding_story.worker --lane <lane>
```

## API·worker 환경변수

API와 세 worker에 같은 DB·서비스 연동·Provider 설정을 전달한다. `(Secret)` 표시는 비밀 관리 대상이다.

### 실행 환경

- `APP_ENV` — dev는 `dev`, prod는 `prod`.
- `MODEL_PROFILE` — dev/prod 모두 `runtime`.

### PostgreSQL

- `DB_HOST`, `DB_PORT`, `DB_NAME` — 기존 AI용 PostgreSQL의 내부 주소·포트·DB명.
- `DB_USERNAME`, `DB_PASSWORD` **(Secret)** — AI runtime DB 계정.
- `DB_SSLMODE` — DB 연결에 TLS 설정이 필요한 경우 지정.

### BE 연동

- `AI_SERVICE_TOKEN` **(Secret)** — BE → AI 요청의 Bearer token. BE가 보내는 값과 동일해야 한다.
- `PROJECT_SERVICE_BASE_URL` — AI → BE 요청에 사용할 내부 URL.
- `INTERNAL_API_KEY` **(Secret)** — AI → BE 업로드 대상 요청·완료 callback에 사용하는 `X-Internal-Api-Key`. BE 검증값과 동일해야 한다.

### Google Vertex AI (ADC)

- `GOOGLE_CLOUD_PROJECT` — Gemini 호출 대상 GCP project ID.
- `GOOGLE_CLOUD_LOCATION` — Vertex AI 호출 위치. 코드 기본값은 `global`.
- `GOOGLE_APPLICATION_CREDENTIALS` — Pod에 마운트한 Google `external_account` 설정 파일의 **경로**. 예: `/var/run/secrets/google-wif/credentials.json`.

### OpenAI 이미지 생성 (WIF)

- `OPENAI_IDENTITY_PROVIDER_ID` — OpenAI에 등록된 WIF provider ID.
- `OPENAI_SERVICE_ACCOUNT_ID` — 연결된 OpenAI project service account ID.
- `OPENAI_WIF_AUDIENCE` — OpenAI용 projected token의 audience. 현재 예시값은 `https://api.openai.com/v1`.
- `OPENAI_WIF_TOKEN_FILE` — Pod에 마운트한 OpenAI용 projected token의 **경로**. 예: `/var/run/secrets/openai-wif/token`.

## BE 측 배포값과 HTTP 계약

- **배포 환경변수:** `FUNDING_STORY_AI_URL`은 AI 내부 주소, `FUNDING_STORY_AI_TOKEN`은 AI의 `AI_SERVICE_TOKEN`과 같은 값이다. BE의 `INTERNAL_API_KEY`는 AI가 BE로 보내는 내부 키와 일치해야 한다.
- **HTTP 계약:** `X-Project-Id`는 요청할 때 전달하는 프로젝트 ID 헤더다. 업로드 대상 발급·완료 callback의 경로와 요청·응답 형식, presigned URL 발급도 API 연동 계약이다. 이들은 환경변수 값이 아니다.

## 환경변수 외에 필요한 설정

- **Google WIF/ADC:** EKS ServiceAccount의 OIDC 토큰을 신뢰하는 Google Workload Identity Pool/Provider와 Vertex AI 권한이 필요하다. `GOOGLE_APPLICATION_CREDENTIALS`가 가리키는 `external_account` JSON에는 Google용 토큰 파일 경로가 들어간다. Google 서비스 계정 private key는 사용하지 않는다.
- **OpenAI WIF:** EKS OIDC issuer와 정확한 ServiceAccount subject(`system:serviceaccount:<namespace>:<name>`)를 OpenAI provider·service account에 매핑해야 한다. dev/prod에서는 `OPENAI_API_KEY`를 사용하지 않는다.
- **EKS 토큰 마운트:** Google과 OpenAI용 토큰은 각 provider에 맞는 audience와 서로 다른 파일 경로로 마운트한다. 이미지는 비루트 사용자로 실행되므로 두 파일의 읽기 권한도 맞춰야 한다.
- **배포 의존성:** EKS의 ECR 이미지 pull 권한, AI DB와 BE 내부 URL 연결, Google·OpenAI 및 BE가 발급한 presigned URL 호스트로 나가는 통신이 필요하다. `db/migration/V*__*.sql`은 API 이미지에 없으므로 DB migration은 SQL을 제공하는 별도 Flyway Job이 필요하다.

GitHub Actions의 ECR push용 AWS IAM Role은 **CI 인증**에 사용된다. 위 Google/OpenAI WIF는 **EKS 런타임 인증**이다. WIF의 신뢰·권한 설정과 ADC 설정 파일은 환경변수만으로 만들어지지 않는다.

## 담당 구분

**AI 팀**

- API·worker 코드, 사용할 모델, 환경변수 이름, DB migration SQL, 인증 점검 도구를 제공한다. 앱의 ADC/WIF 사용 코드는 준비되어 있다.
- 사용할 GCP project와 OpenAI 조직·project를 정하고, 해당 관리자와 모델 호출 권한 및 WIF 등록을 협의한다. 로컬 개인 로그인 정보·API key를 Pod에 전달하지 않는다.
- BE가 보내는 `AI_SERVICE_TOKEN`을 검증하고, AI → BE 요청에 `INTERNAL_API_KEY`를 사용한다. 배포 후 실제 Gemini·OpenAI 호출을 확인한다.

**BE 팀 (Project Service)**

- 위 BE 배포값을 사용해 AI 내부 API를 호출하며, 요청의 프로젝트 ID를 `X-Project-Id`로 전달한다.
- AI의 업로드 대상 요청·완료 callback을 수신하고 `X-Internal-Api-Key`를 검증한다. 이미지 입출력용 presigned URL과 결과 저장을 담당한다.
- 양방향 인증값과 요청·응답 계약을 AI 팀과 맞춘다. AI DB에는 직접 접근하지 않는다.

**인프라팀 / GitOps**

- EKS namespace·ServiceAccount·OIDC issuer를 확정하고, Google/OpenAI용 projected token을 각 audience에 맞춰 읽을 수 있는 경로로 마운트한다.
- ECR pull, API·worker 배포, 양쪽 서비스의 환경변수·Secret 참조, Google ADC 설정 파일 mount, DB·BE·외부 Provider 네트워크와 별도 Flyway Job을 구성한다.
- EKS의 정확한 issuer·ServiceAccount subject·audience를 Google/OpenAI 설정 담당자에게 전달한다.

Google Workload Identity Pool/Provider·Vertex AI 권한과 OpenAI WIF provider·service account 매핑은 해당 계정의 관리자 권한이 필요하다. 인프라팀에 권한이 없다면 AI 팀이 계정 관리자와 진행하고, 인프라팀은 EKS 신원값을 제공한다.

상세 기술 계약: [EKS Provider 인증](https://github.com/KT-Cloud-Tech-Up-team6/Fundit-AI-Funding-Story/blob/main/docs/aws-eks-provider-auth.md) · [설정 코드](https://github.com/KT-Cloud-Tech-Up-team6/Fundit-AI-Funding-Story/blob/main/src/funding_story/config.py)
