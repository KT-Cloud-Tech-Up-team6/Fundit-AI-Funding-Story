# Funding Story AI — 인프라팀 전달 자료

EKS 배포에 필요한 자료는 아래 링크를 전달합니다. 환경별 실제 값이 확정되기 전에는 `<...>`를 유지합니다.

| 자료 | 용도 |
| --- | --- |
| [환경변수 목록](funding-story-ai-env.md) | AI API와 worker에 주입할 변수, Secret 구분 |
| [ADC·WIF 인증 계약](aws-eks-provider-auth.md) | Google·OpenAI 신뢰 설정, 토큰 audience와 파일 경로 |
| [EKS 마운트 예시](../deploy/examples/eks-provider-auth.yaml) | ServiceAccount, projected token, ADC 설정 파일 마운트 예시. 실제 배포 매니페스트는 GitOps에서 작성 |

## 환경별 인증값 교환

먼저 인프라팀에서 dev/prod별 **EKS OIDC issuer URL, namespace, AI 전용 ServiceAccount 이름**을 확정해 전달합니다. AI팀은 이를 기준으로 Google·OpenAI 계정 관리자와 신뢰 설정을 진행합니다.

설정 후 AI팀이 인프라팀에 전달할 값은 다음과 같습니다.

| 대상 | 전달값 |
| --- | --- |
| Google | GCP project ID, Workload Identity Pool/Provider 전체 경로, 필요시 impersonation할 Google service account, 해당 환경의 `external_account` ADC 설정 JSON |
| OpenAI | identity provider ID, project service account ID, projected token audience |
| EKS 적용 | Google·OpenAI 토큰과 ADC JSON의 Pod 내 마운트 경로. 위 마운트 예시와 동일하게 맞춤 |

인프라팀은 확정된 값으로 EKS ServiceAccount·토큰·ADC 파일을 마운트하고 환경변수를 주입합니다. AI팀은 실제 Pod에서 Google·OpenAI 호출을 확인합니다. 현재 저장소의 YAML과 문서는 **환경값이 없는 예시**이며, 계정 측 신뢰 설정이 완료됐다는 의미는 아닙니다.

DB 비밀번호와 서비스 간 토큰은 [환경변수 목록](funding-story-ai-env.md)의 Secret 항목에 해당합니다. 원문을 GitHub·Notion·Discord에 적지 말고 승인된 Secret 관리 경로로 전달합니다. 로컬 개인 계정 로그인 정보도 배포에 사용하지 않습니다.
