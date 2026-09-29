# Funding Story AI — 인프라팀 전달 자료

인프라팀 전달 페이지: [Notion 환경변수](https://app.notion.com/p/Funding-Story-AI-3e59e3e335cc8007ab1eeda4d10da4cf). 아래 파일은 같은 내용의 저장소 원본입니다.

| 자료 | 용도 |
| --- | --- |
| [DB 마이그레이션](funding-story-ai-migration.md) | Flyway 이미지·SQL·Job 변수·적용 순서 |
| [환경변수 목록](funding-story-ai-env.md) | AI API와 worker에 주입할 변수, Secret 구분 |
| [WIF 인증 계약](aws-eks-provider-auth.md) | ADC·WIF 역할, Google·OpenAI 신뢰 설정, 토큰 audience와 파일 경로 |
| [Google dev 설정 JSON](../deploy/auth/google-wif-dev.json) | 비밀키 없는 `external_account` 설정. Notion에도 파일 첨부 |
| [EKS 마운트 예시](../deploy/examples/eks-provider-auth.yaml) | ServiceAccount, projected token, Google WIF 설정 JSON 마운트 예시. 실제 배포 매니페스트는 GitOps에서 작성 |

Google은 **ADC로 WIF 설정 JSON을 찾아 WIF 인증**을 수행하고, OpenAI는 별도 SDK 설정으로 WIF 인증을 수행합니다. Google 설정 JSON에는 서비스 계정 비밀키가 없습니다.

## 환경별 인증값 교환

2026-09-29 **dev EKS 정보 수신을 완료했습니다.** Namespace는 `dev`, ServiceAccount는 `funding-story-ai`, subject는 `system:serviceaccount:dev:funding-story-ai`입니다. API와 worker 3종에 동일하게 적용합니다. OIDC issuer는 [인증 계약의 확정된 dev EKS 정보](aws-eks-provider-auth.md#확정된-dev-eks-정보)에 기록했습니다.

이 값을 기준으로 Google·OpenAI 신뢰·권한 설정을 완료했습니다. prod 인증 주체는 별도로 확정합니다.

## 현재 준비 상태

1. **Google 완료:** `project-c0d4f6ae-c737-445d-93b`에 Pool `funding-story-dev`, Provider `eks-dev`, 서비스 계정 `funding-story-ai-dev`를 생성했습니다. 정확한 dev subject에만 서비스 계정 사용 권한을 부여했고, 모델 호출 권한은 `aiplatform.endpoints.predict`·`serviceusage.services.use`로 제한했습니다.
2. **OpenAI 완료:** `develop` / `Project` (`proj_lDOwnNKCktKIkpbWtKjczI76`)에 Provider `funding-story-dev-eks`와 서비스 계정 `funding-story-ai-dev`를 연결했습니다. 정확한 dev subject와 `api.model.request` 권한만 허용합니다. 실제 ID는 환경변수 문서·Notion에 반영했습니다.
3. **Google 전달 파일 완료:** `google-wif-dev.json`을 생성해 Notion에 첨부했습니다. Google의 확정된 값과 마운트 경로를 환경변수 문서·예시에 반영했습니다.

Google 설정은 다시 조회해 Provider 활성 상태·권한 연결·사용자 관리 키 미생성을 확인했습니다. OpenAI도 저장 후 프로젝트·서비스 계정 ID·매핑 조건·권한을 확인했습니다. 실제 EKS 토큰 교환·모델 호출은 아직 수행하지 않았습니다.

## 전달값

| 대상 | 전달값 |
| --- | --- |
| Google | GCP project ID, Workload Identity Pool/Provider 전체 경로, Google projected token audience, 대상 Google service account, AI팀이 생성한 `external_account` 설정 JSON |
| OpenAI | identity provider ID, project service account ID, projected token audience |
| EKS 적용 | Google·OpenAI 토큰과 Google WIF 설정 JSON의 Pod 내 마운트 경로. 위 마운트 예시와 동일하게 맞춤 |

인프라팀은 확정된 값으로 EKS 토큰·설정 JSON을 마운트하고 환경변수를 주입합니다. Google·OpenAI 실제 dev 값은 문서와 YAML 예시에 반영됐습니다. YAML은 인증 설정 예시이며, 실제 배포 매니페스트는 GitOps에서 관리합니다.

DB·Flyway Job·Secret 참조·GitOps 배포는 인프라팀이 준비합니다. DB 접속 정보와 서비스 간 토큰은 BE·인프라팀이 관리하고 주입합니다. AI팀은 Google·OpenAI 인증 설정값·Google 설정 JSON과 DB 마이그레이션 이미지·적용 문서를 제공합니다.
