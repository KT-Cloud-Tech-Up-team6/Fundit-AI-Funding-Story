# Funding Story AI — AWS 배포·운영 인프라 인계

기준: `Fundit-AI-Funding-Story`의 현재 `main` 코드. 대상은 dev/prod EKS이며, 실제 적용 매니페스트와 환경별 값은 `Fundit-GitOps`에서 관리한다. 이 문서는 값을 전달하고 준비 작업을 합의하기 위한 계약이다. 계정 비밀번호나 토큰 원문은 이 문서와 Git에 기록하지 않는다.

## 먼저 구분할 인증 세 가지

| 흐름 | 주체와 목적 | 환경변수로 전달하는 것 | 환경변수 외에 필요한 것 |
|---|---|---|---|
| GitHub OIDC → AWS IAM | `main` push의 GitHub Actions가 AI 전용 ECR에 이미지를 **업로드** | 런타임 Pod에는 해당 없음 | 이미 준비된 `fundit-ai-funding-story-ci-role`, GitHub OIDC 신뢰 정책, ECR push 권한. GitHub workflow에 `environment:`나 AWS Access Key를 추가하지 않음 |
| EKS ServiceAccount token → Google WIF → ADC | API·worker가 Vertex AI Gemini를 호출 | `GOOGLE_APPLICATION_CREDENTIALS`는 **credential configuration JSON의 파일 경로**; `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`은 호출 대상 | EKS issuer·subject를 신뢰하는 Google Workload Identity Pool/Provider, Google IAM 권한, audience가 맞는 projected token, 이를 참조하는 `external_account` JSON과 읽기 전용 mount |
| EKS ServiceAccount token → OpenAI WIF | API·worker가 OpenAI 이미지를 생성 | `OPENAI_IDENTITY_PROVIDER_ID`, `OPENAI_SERVICE_ACCOUNT_ID`, `OPENAI_WIF_AUDIENCE`는 식별자·audience; `OPENAI_WIF_TOKEN_FILE`은 **토큰 파일 경로** | OpenAI 조직의 provider·service account mapping·모델 호출 권한, audience가 맞는 별도 projected token mount |

**WIF는 외부 서비스가 EKS workload 신원을 신뢰하고 단기 자격 증명으로 교환하는 구성 방식이고, ADC는 Google SDK가 자격 증명을 찾는 방식이다.** 관련 환경변수는 이 구성을 가리킬 뿐 신뢰 관계나 권한을 만들지 않는다. Google ADC 설정 파일은 `external_account` 형식이며 서비스 계정 private key를 담지 않는다. Google과 OpenAI용 projected token은 같은 Kubernetes ServiceAccount에서 발급받더라도 audience와 경로를 분리한다. ECR push용 CI Role은 두 런타임 인증과 별개다. [Google ADC](https://docs.cloud.google.com/docs/authentication/application-default-credentials), [Google의 Kubernetes WIF](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-kubernetes), [OpenAI의 AWS WIF](https://developers.openai.com/api/docs/guides/workload-identity-federation/aws)

## 배포 대상과 이미지

| 항목 | 계약 |
|---|---|
| ECR | `899957568205.dkr.ecr.ap-northeast-2.amazonaws.com/fundit-ai-funding-story` |
| 게시 | `main` push 시 테스트 후 `sha-<전체 commit SHA>` 태그로 게시. 현재 워크플로는 **이미지 생성·업로드까지만** 수행 |
| API | 동일 Docker 이미지의 기본 명령; 컨테이너 포트 `8000`; liveness `GET /health`, readiness `GET /health/ready` |
| worker | 같은 이미지에 `/app/.venv/bin/python -m funding_story.worker --lane <lane>` 명령 지정. `funding-story`, `page-summary`, `storyline`을 각각 별도 Deployment로 운영; 초기 `--concurrency 1` |
| DB migration | API 이미지에는 `db/migration` SQL이 복사되지 않음. SQL을 별도 Flyway Job에 제공하고 API·worker보다 먼저 실행·검증 |
| ECR pull | EKS 노드/Pod의 이미지 **pull** 권한과 GitOps의 이미지 tag/digest 갱신 필요. CI push Role을 런타임에 재사용하지 않음 |

첫 `main` 게시 검증 기록(2026-09-24): [GitHub Actions 실행](https://github.com/KT-Cloud-Tech-Up-team6/Fundit-AI-Funding-Story/actions/runs/35858718035), 태그 `sha-2f504a1193e794b58bf5b9f463fe9d9c6a4f75ba`, digest `sha256:dbce29f7df00c7e89172773420664d480a891a3aaa8c8c90c3dee57272f76746`. 이는 ECR 업로드 검증 결과이며 EKS 배포 완료를 뜻하지 않는다.

## API·worker에 주입할 환경변수

값은 환경마다 달리 확정한다. 아래의 “Secret”은 Kubernetes Secret 또는 조직이 채택한 비밀 관리 체계의 참조를 뜻한다. API와 세 worker에 동일한 연결·Provider 값을 적용한다.

| 이름 | 전달할 값 또는 형식 | 주입·확정 주체 |
|---|---|---|
| `APP_ENV` | dev는 `dev`, prod는 `prod` | GitOps ConfigMap |
| `MODEL_PROFILE` | dev/prod 모두 `runtime` | GitOps ConfigMap |
| `DB_HOST`, `DB_PORT`, `DB_NAME` | 기존 AI용 CNPG 논리 DB의 내부 주소·포트·DB명. Project Service DB와 분리 | Infra 확정 → GitOps ConfigMap |
| `DB_USERNAME`, `DB_PASSWORD` | AI runtime DB 계정. 로컬 예시 계정·비밀번호 사용 금지 | Infra Secret → GitOps 참조 |
| `DB_SSLMODE` | CNPG TLS 정책에 맞는 값이 필요한 경우에만 지정 | Infra 확정 → GitOps ConfigMap |
| `AI_SERVICE_TOKEN` | **BE → AI** 요청의 `Authorization: Bearer` 인증값; BE 설정값과 일치 | BE·Infra 합의 → Secret |
| `PROJECT_SERVICE_BASE_URL` | **AI → BE** 내부 DNS URL. `localhost` 사용 금지 | BE·Infra 확정 → GitOps ConfigMap |
| `INTERNAL_API_KEY` | **AI → BE** 업로드 대상 발급·완료 callback의 `X-Internal-Api-Key`; BE 검증값과 일치 | BE·Infra 합의 → Secret |
| `GOOGLE_CLOUD_PROJECT` | Vertex AI 대상 GCP project ID | GCP 관리자·Infra 확정 → ConfigMap |
| `GOOGLE_CLOUD_LOCATION` | Vertex AI 호출 위치; 코드 기본값은 `global`, 실제 모델·권한과 대조 | GCP 관리자·Infra 확정 → ConfigMap |
| `GOOGLE_APPLICATION_CREDENTIALS` | mount된 Google `external_account` JSON의 절대 경로. 예: `/var/run/secrets/google-wif/credentials.json` | GitOps ConfigMap + 파일 mount |
| `OPENAI_IDENTITY_PROVIDER_ID` | OpenAI 조직에 등록한 EKS WIF provider ID | OpenAI 관리자 확정 → ConfigMap/정책상 Secret |
| `OPENAI_SERVICE_ACCOUNT_ID` | 해당 provider와 매핑한 OpenAI project service account ID | OpenAI 관리자 확정 → ConfigMap/정책상 Secret |
| `OPENAI_WIF_AUDIENCE` | OpenAI provider 및 projected token과 동일한 audience. 저장소 예시: `https://api.openai.com/v1` | OpenAI 관리자·Infra 합의 → ConfigMap |
| `OPENAI_WIF_TOKEN_FILE` | OpenAI용 projected token 파일의 절대 경로. 예: `/var/run/secrets/openai-wif/token` | GitOps ConfigMap + token mount |

`AI_SERVICE_TOKEN`은 AI가 **받는** 요청, `INTERNAL_API_KEY`는 AI가 BE로 **보내는** 요청에 사용한다. 둘을 같은 값이라고 가정하지 않는다. 이미지는 BE가 발급한 presigned URL로 읽고 업로드하므로 이 AI 서비스에 직접 S3 Access Key나 버킷 환경변수를 만들지 않는다.

### 기본값으로 시작할 수 있는 조정값

| 변수 | 코드 기본값 | 조정 기준 |
|---|---:|---|
| `DB_POOL_MIN_SIZE`, `DB_POOL_MAX_SIZE` | `1`, `5` | CNPG 연결 한도·API/worker replica 수에 따라 결정 |
| `DB_POOL_TIMEOUT_SECONDS`, `DB_POOL_MAX_IDLE_SECONDS`, `DB_POOL_MAX_LIFETIME_SECONDS` | `10`, `300`, `1800` | DB 연결 관측 후 변경 |
| `FUNDING_STORY_STATE_TTL_SECONDS` | `86400` | 기록 보존·정리 정책과 합의 |
| `INTERNAL_HTTP_TIMEOUT_SECONDS`, `COMPLETION_CALLBACK_ATTEMPTS` | `20`, `3` | BE timeout·callback 재시도 정책과 합의 |
| `IMAGE_GENERATION_CONCURRENCY`, `RENDER_CONCURRENCY` | `4`, `2` | worker CPU·메모리·모델 호출 한도에 맞춰 조정 |
| `IMAGE_GENERATION_ATTEMPTS`, `IMAGE_RETRY_DELAY_SECONDS`, `IMAGE_RETRY_MAX_DELAY_SECONDS` | `5`, `2`, `30` | 외부 API 429/5xx 관측 후 변경 |
| `IMAGE_REQUEST_INTERVAL_SECONDS`, `IMAGE_REQUEST_MAX_INTERVAL_SECONDS`, `IMAGE_GENERATION_BUDGET_SECONDS` | `0.25`, `20`, `3600` | 처리시간·호출량 관측 후 변경 |

`DATABASE_URL`은 임시 호환용 override이므로 신규 배포에는 `DB_*`를 사용한다. `MODEL_PROFILE=runtime`이 텍스트·이미지 모델과 인증 방식을 고정하므로 `TEXT_MODEL`, `IMAGE_PROVIDER`, `IMAGE_MODEL`, `IMAGE_QUALITY`, `OPENAI_AUTH_MODE`를 별도로 덮어쓰지 않는다. `OPENAI_API_KEY`는 로컬 smoke 용도이며 dev/prod에 배포하지 않는다. `PRETENDARD_FONT_PATH`와 Chromium은 Docker 이미지에 준비되어 있다. `OPENAI_WIF_EXPECTED_SUBJECT`는 선택적 **오프라인 인증 점검 명령**의 검증값이며 앱 필수 설정은 아니다.

## 환경변수 외에 준비할 배포 리소스

| 영역 | 준비·검증할 내용 | 주 담당 |
|---|---|---|
| 신원 | 환경별 AI 전용 namespace·Kubernetes ServiceAccount, EKS OIDC issuer와 정확한 `system:serviceaccount:<namespace>:<name>` subject, OpenAI/Google 각각의 신뢰 설정·권한 매핑. OpenAI mapping에는 최소 `api.model.request` 권한 확인 | Infra + GCP/OpenAI 관리자 |
| 마운트 | Google/OpenAI용 서로 다른 audience의 회전형 projected token, Google `external_account` JSON. JSON 내부 `credential_source.file`이 실제 Google token 경로를 가리키도록 설정 | GitOps + Infra |
| 파일 권한 | Docker 이미지는 비루트 `app` 사용자로 실행한다. 예시 매니페스트의 projected token `defaultMode: 0400`은 실행 UID가 소유자가 아닐 경우 읽지 못할 수 있다. 실제 Pod의 UID/GID·`fsGroup`·파일 mode를 함께 정하고 **API·세 worker 내부에서 읽기 확인** | GitOps + Infra |
| DB | 기존 AI CNPG DB·Secret·네트워크·백업·복구 지점, runtime DML 계정과 migration DDL 계정 분리 여부, 연결 한도 | Infra + GitOps |
| migration | `db/migration/V*__*.sql`을 제공하는 별도 Flyway Job; DB backup → 사전 구조 확인 → `migrate` → `validate`. 기존 DB baseline은 구조가 맞는 경우에만 명시적으로 수행 | GitOps + AI + Infra |
| 워크로드 | API Service와 네 개 Deployment(API + worker 3종), 환경별 replica·CPU·메모리·롤링 정책. worker에는 HTTP health endpoint가 없으므로 API probe를 그대로 복사하지 않음 | GitOps |
| 네트워크 | BE → AI 내부 통신, AI → BE 내부 DNS, CNPG, Google STS·필요 시 IAMCredentials·Vertex AI, OpenAI API, BE가 제공한 presigned URL의 호스트로 나가는 통신. AI API는 내부 접근으로 제한 | Infra + GitOps + BE |
| 운영 | API·worker 로그/지표, lane별 대기·실패·재시도·비용 경보, DB 및 Secret 회전·롤백 절차. 토큰·presigned URL·프롬프트 원문은 로그에서 제외 | Infra + AI + GitOps |

Google credential configuration JSON은 **경로와 federation 설정을 담는 파일**이며 서비스 계정 private key가 아니다. 실제 GitOps 예시는 Secret volume을 사용하므로 저장 방식은 조직 정책에 맞춰 결정한다. [`deploy/examples/eks-provider-auth.yaml`](../deploy/examples/eks-provider-auth.yaml)은 계약 예시로, 그대로 적용하는 완성된 매니페스트가 아니다. [Google의 external-account 구성 안내](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-kubernetes)

Flyway Job의 변수는 **애플리케이션 API·worker 변수와 별도**다. Job에는 `FLYWAY_URL=jdbc:postgresql://<DB_HOST>:<DB_PORT>/<DB_NAME>`, `FLYWAY_USER`, `FLYWAY_PASSWORD`(migration 계정 Secret), `FLYWAY_LOCATIONS=filesystem:/flyway/sql`을 주입하고 SQL을 해당 경로에 제공한다. 로컬 기준 이미지 버전은 `flyway/flyway:13.7.0`이며 배포 Job의 버전은 AI·GitOps가 함께 고정한다. 런타임 연결 예산은 `DB_POOL_MAX_SIZE × (API replica 수 + worker replica 수 합계) + migration/운영 여유분 ≤ CNPG 허용 연결 수`로 계산한다.

## 적용·검증 순서

1. Infra/GCP/OpenAI 관리자가 환경별 issuer·subject·audience·권한을 확정하고, GitOps가 ServiceAccount·mount·Secret 참조를 작성한다. EKS pull 권한과 네트워크 경로를 확인한다.
2. AI DB 백업·연결 한도를 확인하고 별도 Flyway Job으로 migration과 `validate`를 끝낸다. 실패하면 API·worker rollout을 중단한다.
3. GitOps가 해당 `sha-<전체 commit SHA>` 이미지 tag 또는 digest를 API·세 worker에 지정한다. API의 `/health/ready`와 worker 프로세스 기동·DB 연결을 확인한다.
4. Pod 내부에서 `/app/.venv/bin/python -m funding_story.auth_check`를 실행한다. 이 명령은 파일·JWT claim 구조를 확인하며 **서명, 실제 federation 교환, 외부 권한까지 보증하지 않는다**.
5. dev EKS에서 Gemini 응답 1건과 OpenAI 이미지 1건을 실제 호출하고, BE → AI 인증·AI → BE callback 및 presigned URL 입출력을 확인한다. 세 worker lane이 각각 작업을 회수하는지 확인한다.
6. 대기시간·실패율·DB 연결·외부 API 비용을 관측한 뒤 prod replica와 동시성을 확정한다. 문제 시 BE 신규 trigger를 먼저 멈추고 작업 상태를 확인한 뒤 worker·API를 롤백한다.

## 전달받아야 할 환경별 확정값

| 묶음 | 필요한 값 |
|---|---|
| EKS/GitOps | dev/prod namespace·ServiceAccount, EKS issuer, ECR pull 주체, GitOps 저장소·적용 경로, API 내부 DNS·replica·리소스 |
| Google | GCP project·location, Workload Identity Pool/Provider·token audience·권한 대상, `external_account` JSON 생성·보관 위치 |
| OpenAI | provider ID, service account ID, token audience, 정확한 subject mapping, 이미지 모델 호출 권한 |
| DB | CNPG host/port/name, runtime/migration 계정 Secret 참조, TLS 정책, 연결 한도, 백업·baseline 상태 |
| BE | Project Service 내부 URL, 양방향 인증값의 Secret 참조, presigned URL의 도메인·접속 경로 |
| 운영 | worker별 replica·동시성·자원, 알림 채널, Secret 회전·롤백 담당 |

세부 인증 계약은 [EKS Provider 인증](aws-eks-provider-auth.md), 환경변수의 코드 기본값은 [`config.py`](../src/funding_story/config.py), worker 운영은 [Content Insights 운영 안내](content-insights-operations.md), 이미지 게시 조건은 [개발 환경과 실행](development.md#github-actions-이미지-게시)을 따른다.
