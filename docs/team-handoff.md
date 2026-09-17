# Funding Story AI 팀별 인계 사항

기준: AI API 0.2.0, 2026-09-17. 이 문서는 AI팀 제공 범위와 타팀 후속 작업을 분리한다.

## 현재 AI팀 제공물

- FastAPI/OpenAPI: 자산, 세션, 메시지/SSE, 확인, 생성/조회/재시도, PNG export, 저장 commit.
- LangGraph: 대화 review와 생성 파이프라인, PostgreSQL checkpoint, 형식 오류 피드백 재시도.
- 출력: 블록별 PNG 자산 매니페스트, 프로젝트 예산~신뢰와 안전 입력 텍스트, 공통 안내 key, 후속 AI용 summary/storyline.
- 안정성: 프로젝트 범위, revision, message/idempotency key, 공급자/형식 재시도 분리, 성공 이미지 재사용.
- 수명주기: BE 저장 성공 전 임시 후보 보존, commit 후 scene·snapshot·중간 생성 이미지·run checkpoint 정리.
- 검증: 55개 자동 테스트, 로컬 HTTP health/auth/OpenAPI, 패키지 빌드. application 단위 테스트는 DB 없이 실행하며 DB 통합 테스트는 격리된 PostgreSQL 17을 사용한다. Docker 이미지 실행은 미검증.

## 프론트엔드팀

`ProjectStoryForm → StoryEditor → FundingStoryModal` 디자인을 유지하고 목업 reducer/timer만 실제 controller로 교체한다. 구체적인 화면/API 매핑은 [프론트 호출 대응표](frontend-call-contract.md)를 따른다.

필요 작업:

1. session/revision/message/run/export ID를 프로젝트별로 관리한다.
2. 채팅은 SSE, 생성은 polling으로 연결한다.
3. 요약과 제품의 핵심 강점 수정·정렬·삭제를 채팅 요청으로 전달한다.
4. 결과 모달의 문구 수정값을 node ID 기반 `text_overrides`로 export한다.
5. `images` 순서대로 Tiptap에 넣고 `information`은 일반 편집 텍스트로 연결한다.
6. `setContent` 전체 덮어쓰기 전에 적용 범위와 작성 중인 본문 처리 UX를 확정한다.
7. BE 저장 성공 뒤에만 export commit을 호출한다.

Polotno, 요소 이동·크기·블록 높이 편집은 초기 연결의 필수 조건이 아니다.

## 백엔드팀

필요 작업:

1. Gateway 인증과 프로젝트 소유권 확인 뒤 AI 내부 API를 중계한다.
2. 등록 프로젝트·선물·승인된 자산 key를 AI 세션 입력으로 변환한다.
3. PNG `asset_id`를 프로젝트 영구 자산으로 연결하고 Tiptap 본문/정보 텍스트를 하나의 revision으로 저장한다.
4. 본문 저장 커밋 이후 AI export commit을 호출한다. 저장 실패·충돌 시 호출하지 않는다.
5. commit 응답 유실 시 같은 `document_revision`으로 재시도한다.
6. `fixed_content`의 공통 안내 key를 서비스 정책 콘텐츠로 해석한다.

BE에 Konva JSON 저장·부분 재생성 코드가 있다면 PNG+일반 텍스트 계약과 충돌하는지 검토한다.

## 기획·디자인팀

- 결과 모달에서 허용할 초기 편집은 문구 수정이다.
- 이미지 슬롯 실패·`input_required`, 전체 재생성, 뒤로 가기, 저장 실패 상태의 문구와 동작을 확정한다.
- 공통 크라우드 펀딩 안내 문안과 key 버전을 확정한다. 현재 `pending-v1`은 운영 문안이 아니다.
- 프로젝트 소개/선물 PNG와 하단 정보 텍스트가 같은 폭으로 이어지는 최종 읽기 화면을 검토한다.
- 선물 1~2종에서는 3카드 디자인을 유지하고 미등록 칸을 표시한다. 현재 제한된 편집 범위에서의 사용 안내를 확인한다.

## 인프라·보안팀

- API·Celery worker·PostgreSQL·broker 배치, worker 동시성과 timeout을 확정한다.
- 프로젝트 입력/생성/최종 PNG의 S3 prefix·권한·보존 기간과 CDN 조회 방식을 제공한다.
- Chromium 실행 리소스와 Pretendard 파일 공급·Docker build의 외부 다운로드 접근 정책을 결정한다.
- AI service token, Gateway 내부 인증, egress, Google ADC와 Secret 주입을 연결한다.
- LangSmith의 입력·출력 수집 범위·마스킹·보존·접근 권한을 검토한다.

## 전달 순서

1. [OpenAPI](openapi.json)와 [샘플 export](examples/export-result.json)를 FE·BE에 공유한다.
2. FE/BE가 mock consumer로 요청·응답을 고정한다.
3. 로컬 통합 환경에서 FE → BE → AI를 연결한다.
4. 신규 프로젝트 등록부터 저장·재조회까지 E2E를 수행한다.
5. 디자인·기획이 실제 화면과 고정 문안을 승인한 뒤 운영 환경 검증으로 이동한다.
