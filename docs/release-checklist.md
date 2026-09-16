# GitHub 업로드 준비

대상 이름: `Fundit-AI-Funding-Story`. AI API 저장소 단독으로 준비한다. 사용자 승인으로 조직 저장소 생성·연결 및 main push를 완료했다. PR·팀 메시지 발송은 수행하지 않았다.

## 포함

- FastAPI·LangGraph·Celery 구현과 템플릿/Konva 런타임
- pyproject/uv.lock, Python 버전, 환경변수 예시
- PostgreSQL/Redis 개발 Compose, Dockerfile, GitHub Actions
- 테스트·가상 제품 fixture, checksum 기반 폰트 설치 도구
- 영문/한국어 README, API 계약·개발 안내·검증 범위·팀 인계·OpenAPI·응답 예시

## 제외

- FE/BE 소스와 해당 저장소 변경
- 실제 `.env`, ADC/키/토큰, DB·Redis 상태, 생성 이미지·PNG·대화 기록
- 로컬 프로젝트 수정용 SQL·BE 검사 스크립트(`scripts/local/`)
- 브라우저 로그, 실험 출력, 상세 과거 작업 이력(`output/`)

## 점검

- [x] 독립 디렉터리 설치·테스트·패키징
- [x] Markdown 상대 링크·OpenAPI 일치
- [x] 업로드 파일/비밀정보 제외 점검
- [x] 로컬 변경 커밋
- [x] 조직 공개 저장소 생성 및 origin 지정: https://github.com/KT-Cloud-Tech-Up-team6/Fundit-AI-Funding-Story
- [x] main push 완료
- [ ] 원격 CI 성공 확인 — 실행 중

CI에는 PostgreSQL, Chromium, Pretendard 설치를 포함한다. 원격 실행 전까지 녹색 CI 결과를 주장하지 않는다. Docker 실행과 운영 구성은 별도 확인 사항이다.
