# AWS EKS Provider 인증 계약

2026-09-29 전달받은 dev EKS 인증 주체로 Google·OpenAI 신뢰·권한 설정과 Google JSON 생성을 완료했다. 인프라팀은 확정된 설정과 파일 마운트를 GitOps에 적용한다. 실제 EKS 토큰 교환·모델 호출 검증은 아직 수행하지 않았다.

## 확정된 dev EKS 정보

| 항목 | 값 |
|---|---|
| Namespace | `dev` |
| Kubernetes ServiceAccount | `funding-story-ai` |
| OIDC issuer | `https://oidc.eks.ap-northeast-2.amazonaws.com/id/F9803D0BA6D1AF7F02C6DCB6AC3CB308` |
| Subject (`sub`) | `system:serviceaccount:dev:funding-story-ai` |
| 적용 대상 | API와 worker 3종이 같은 ServiceAccount 사용 |

위 값은 dev 기준이다. prod 인증 주체는 별도로 확정한다. Google·OpenAI의 배포용 서비스 계정은 위 Kubernetes ServiceAccount와 별개이며, 정확한 issuer·subject를 신뢰하도록 연결해야 한다.

Google `external_account` 설정 JSON은 AI팀이 생성한 [google-wif-dev.json](../deploy/auth/google-wif-dev.json)을 전달한다. 인프라팀은 이 JSON과 Google·OpenAI 각각의 audience를 가진 EKS 토큰을 마운트한다. 서비스 계정 private key JSON을 전달하는 방식은 사용하지 않는다.

## Google dev 설정값

| 항목 | 값 |
|---|---|
| Project ID / number | `project-c0d4f6ae-c737-445d-93b` / `254172583499` |
| Pool 경로 | `projects/254172583499/locations/global/workloadIdentityPools/funding-story-dev` |
| Provider 경로 | `projects/254172583499/locations/global/workloadIdentityPools/funding-story-dev/providers/eks-dev` |
| EKS Google 토큰 audience | `https://iam.googleapis.com/projects/254172583499/locations/global/workloadIdentityPools/funding-story-dev/providers/eks-dev` |
| Google 서비스 계정 | `funding-story-ai-dev@project-c0d4f6ae-c737-445d-93b.iam.gserviceaccount.com` |
| Google 토큰 경로 | `/var/run/secrets/google-identity/token` |
| JSON 마운트 경로 | `/var/run/secrets/google-wif/credentials.json` |

Provider 상태는 `ACTIVE`이며 `google.subject=assertion.sub`, `assertion.sub == 'system:serviceaccount:dev:funding-story-ai'` 조건을 적용했다. 위 subject의 principal에만 대상 서비스 계정의 `roles/iam.workloadIdentityUser`를 부여했다.

서비스 계정은 프로젝트 custom role `fundingStoryVertexPredict`의 `aiplatform.endpoints.predict`·`serviceusage.services.use` 권한을 가진다. 사용자 관리 서비스 계정 키는 생성하지 않았다. 설정을 다시 조회해 위 값을 확인했다.

JSON의 `audience`는 `//iam.googleapis.com/...` 형식의 Google Provider 식별자다. EKS projected token의 `audience`는 표의 `https://iam.googleapis.com/...` 값을 그대로 사용한다.

## OpenAI dev 설정값

| 항목 | 값 |
|---|---|
| 조직 / 프로젝트 | `develop` / `Project` (`proj_lDOwnNKCktKIkpbWtKjczI76`) |
| Provider 이름 / ID | `funding-story-dev-eks` / `idp_65c3c65ddba506b5f620bfc7` |
| 서비스 계정 이름 / ID | `funding-story-ai-dev` / `user-1aa935e64b36d873abba6933` |
| EKS 토큰 audience | `https://api.openai.com/v1` |
| EKS 토큰 경로 | `/var/run/secrets/openai-wif/token` |
| 매핑 | `sub` = `system:serviceaccount:dev:funding-story-ai` |
| 매핑 권한 | Restricted / Model capabilities: Request (`api.model.request`) |

위 EKS issuer의 OIDC discovery를 사용한다. Provider와 매핑을 저장한 뒤, 편집 화면에서 프로젝트·서비스 계정 ID와 모델 호출 권한을 확인했다. 새 서비스 계정은 WIF 매핑에서 생성했으며 API key는 발급하지 않았다. 실제 EKS 인증·이미지 호출 검증은 별도다.

## ADC와 WIF의 역할

| 용어 | 역할 |
|---|---|
| ADC | Google 인증 라이브러리가 사용할 인증 정보·설정을 자동으로 찾는 방법 |
| WIF | EKS가 발급한 토큰으로 신원을 증명하고 Google 또는 OpenAI의 단기 토큰을 받는 방식 |

Google 배포에서는 **ADC와 WIF를 함께 사용한다.** ADC가 `GOOGLE_APPLICATION_CREDENTIALS` 경로의 `external_account` 설정 JSON을 찾으면, 인증 라이브러리가 그 설정에 따라 EKS 토큰을 읽어 WIF 인증을 수행한다. Google 서비스 계정을 대신 사용할 권한을 부여했으며, 해당 계정의 단기 토큰으로 Vertex AI를 호출한다.

`external_account` JSON에는 토큰 파일 위치·Google Provider·대상 서비스 계정 등이 들어간다. 비밀키를 포함한 서비스 계정 key JSON과는 다른 파일이며, Google 측 신뢰·권한 설정도 완료해야 동작한다.

| 기능 | Provider | 배포 인증 |
|---|---|---|
| 채팅·문구·Content Insights | Vertex AI Gemini | ADC로 WIF 설정 로드 → EKS 토큰으로 Google WIF 인증 → Vertex AI 호출 |
| 이미지 | OpenAI | EKS 토큰으로 OpenAI WIF 인증 → Images API 호출 |

ADC는 Google 인증 라이브러리의 기능이다. OpenAI SDK는 별도 WIF 설정과 토큰 파일을 사용한다. 로컬 인증은 [개발 환경](development.md#설치와-설정)에 정리한다.

참고: [Google ADC](https://docs.cloud.google.com/docs/authentication/application-default-credentials), [Google Kubernetes WIF](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-kubernetes)

## 런타임 계약

| 값 | 소유 | 주입 방식 |
|---|---|---|
| `MODEL_PROFILE` | AI | dev/prod에서 `runtime` 고정 |
| `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` | AI·인프라 합의 | ConfigMap |
| `GOOGLE_APPLICATION_CREDENTIALS` | 인프라 | GCP external-account JSON mount 경로 |
| Google subject token | EKS | audience가 GCP provider와 일치하는 projected volume |
| `OPENAI_IDENTITY_PROVIDER_ID` | OpenAI 조직 관리자 | ConfigMap 또는 조직 정책에 따른 Secret |
| `OPENAI_SERVICE_ACCOUNT_ID` | OpenAI 조직 관리자 | ConfigMap 또는 조직 정책에 따른 Secret |
| `OPENAI_WIF_AUDIENCE` | AI·인프라 합의 | `https://api.openai.com/v1` |
| `OPENAI_WIF_TOKEN_FILE` | GitOps | `/var/run/secrets/openai-wif/token` |
| OpenAI subject token | EKS | audience가 OpenAI 설정과 일치하는 projected volume |

두 projected token은 같은 Kubernetes ServiceAccount가 발급받아도 audience와 mount 경로를 분리한다. 토큰 원문과 비밀값은 Git에 저장하지 않는다.

이미지는 non-root `app` 사용자로 실행한다. 예시는 `fsGroup: 2000`과 토큰 파일 권한 `0440`으로 읽기 권한을 부여한다. GitOps에서 다른 실행 그룹을 사용하면 이에 맞춰 적용한다. 참고: [Kubernetes security context](https://kubernetes.io/docs/tasks/configure-pod-container/security-context/).

## OpenAI WIF

| 순서 | 담당·작업 | 확인값 |
|---:|---|---|
| 1 | 인프라: AI 전용 Kubernetes ServiceAccount 생성 | `system:serviceaccount:<namespace>:funding-story-ai` |
| 2 | 인프라: EKS cluster OIDC issuer 조회 | token `iss`와 동일 |
| 3 | 인프라: audience `https://api.openai.com/v1` projected token mount | token `aud`와 동일 |
| 4 | AI·OpenAI 관리자: EKS issuer 기반 provider 생성 | `OPENAI_IDENTITY_PROVIDER_ID` |
| 5 | AI·OpenAI 관리자: 정확한 ServiceAccount `sub`를 OpenAI project service account에 매핑 | `OPENAI_SERVICE_ACCOUNT_ID` |
| 6 | AI·OpenAI 관리자: mapping 권한을 최소 `api.model.request`로 제한 | 이미지 API 호출 가능 |

참고: [OpenAI AWS WIF](https://developers.openai.com/api/docs/guides/workload-identity-federation/aws)

## Google WIF (ADC로 설정 로드)

| 순서 | 담당·작업 | 결과 |
|---:|---|---|
| 1 | AI·GCP 관리자: Workload Identity Pool provider가 EKS issuer를 신뢰하도록 구성 | EKS OIDC 검증 |
| 2 | AI·GCP 관리자: `sub`를 AI 전용 ServiceAccount로 제한 | namespace·workload 격리 |
| 3 | AI·GCP 관리자: 대상 GCP service account에 Vertex AI 최소 권한 부여 | Gemini 호출 권한 |
| 4 | AI·GCP 관리자: projected token file을 참조하는 `external_account` JSON 생성 | 장기 private key 없음 |
| 5 | 인프라: JSON과 subject token을 read-only mount하고 `GOOGLE_APPLICATION_CREDENTIALS`에 JSON 경로 지정 | ADC가 WIF 설정을 찾고 인증 라이브러리가 단기 토큰 발급·갱신 |

external-account 설정에는 다음 구조가 필요하다. placeholder는 Google 설정값과 인프라팀이 확정한 EKS 값으로 교체한다.

```json
{
  "type": "external_account",
  "audience": "//iam.googleapis.com/projects/<number>/locations/global/workloadIdentityPools/<pool>/providers/<provider>",
  "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
  "token_url": "https://sts.googleapis.com/v1/token",
  "service_account_impersonation_url": "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/<service-account>:generateAccessToken",
  "credential_source": {
    "file": "/var/run/secrets/google-identity/token"
  }
}
```

## GitOps 예시·검증

적용용 원본은 Fundit-GitOps가 관리한다. AI 저장소 예시는 [`deploy/examples/eks-provider-auth.yaml`](../deploy/examples/eks-provider-auth.yaml)이다.

```sh
# Pod 내부: token 원문·credential 본문을 출력하지 않는 오프라인 검사
/app/.venv/bin/python -m funding_story.auth_check
```

| 검사 | 보장 | 보장하지 않음 |
|---|---|---|
| OpenAI token | JWT 구조·issuer·audience·subject·만료 | 서명·OpenAI mapping·실 API 권한 |
| Google WIF 설정 | `external_account` 형식·subject token file·private key 미사용 | GCP STS exchange·Vertex IAM 권한 |

최종 통합 검증은 dev EKS에서 OpenAI 단일 이미지 호출과 Gemini 단일 구조화 응답 호출로 수행한다.

## 금지

| 금지 항목 | 대안 |
|---|---|
| OpenAI API key를 dev/prod Secret에 저장 | OpenAI WIF |
| GCP service-account JSON private key 저장 | Google WIF 설정 JSON을 ADC로 로드 |
| token 원문 로그·디코드 결과 외부 업로드 | `auth_check`의 제한된 metadata만 사용 |
| 공용 namespace ServiceAccount 매핑 | AI workload 전용 ServiceAccount와 정확한 `sub` |
