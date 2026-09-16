# 검증 범위

기준: 2026-09-16, AI API 0.2.0. 이 문서는 현재 저장소 단독 검증과 실제 모델 사례를 정리한다. 과거 FE/BE 실험·날짜별 작업 기록은 업로드 범위에서 제외했다.

## 자동 검증

- Ruff: `src`, `tests`, `scripts` 검사.
- pytest: 37개 통과. 모델 호출은 mock이며 PostgreSQL·Chromium 렌더링은 실제 사용.
- 세션 진입·메시지·SSE·확인 revision·idempotency·프로젝트 자산 격리.
- 필수 블록/선택 Point·Information 구성, 실패 이미지 슬롯만 재시도.
- PNG export·BE 저장 commit·임시 데이터 정리·이미지 자산 수명주기.
- Konva의 연결 문구 간격, 실제 3줄 탐지, 2줄 계약, PNG 크기.
- 이전 입력에 `gift_details`가 있어도 조립·export에서 제외.

실행법과 환경은 [개발 안내](development.md)를 따른다. 원격 CI 결과와 로컬 테스트는 구분한다.

## 실제 모델·출력 사례

가상 무선청소기 LUMI S1 입력 한 건으로 대화 요약 → 확인 → 생성 → PNG export를 실행했다. 필수 9블록 + Point 4개 + Information = 14블록이다.

- 생성 이미지 17개 성공. 최초 429 실패 2개는 간격을 두고 실패 슬롯만 재시도했다.
- 등록 선물 1종 외 2카드는 input_required로 유지했다. 값을 지어내지 않았다.
- 개발 검토 중 측정 조건 누락·입력에 없는 관리 경고를 발견해 작성 지침을 수정하고 문구를 다시 생성했다. 런타임 자동 사실 교정을 추가한 것은 아니다.
- Pillow 출력의 정렬 차이를 수정하여 Chromium/Konva로 다시 export했다. 같은 이미지를 재사용하고 원 안 문구 1개만 2줄로 다시 작성했다.
- Konva 기준 14블록의 슬롯 높이·명시 폭·줄 수 위반 0건. 폰트 크기 자동 축소 없음.
- 브라우저 1280px/390px에서 14개 PNG 로드 및 가로 넘침 없음 확인.

후속 문구/렌더러 수정은 같은 사례의 재출력이며, 변경할 때마다 전체 신규 생성한 것은 아니다. 로컬 실행 ID·출력 파일·스크린샷은 저장소에 포함하지 않는다.

## 아직 완료로 보지 않는 항목

- 모든 제품군·모든 입력 길이에서의 생성 품질.
- 생성 이미지의 버튼·먼지통 등 제품 세부 형상 정밀 재현.
- 참조 서비스와 모든 픽셀의 동일성.
- Docker 이미지 실행.
- 운영 부하·Chromium 동시성·Gateway/S3/CDN·게시 통합.
- 타팀 최신 FE/BE 코드에서 신규 프로젝트부터 저장·재조회까지의 최종 연동 승인.

공통 크라우드 펀딩 안내 key `fundit.crowdfunding-notice.pending-v1`은 운영 문안 확정 전 값이다.

## 업로드 구성 독립 검증

업로드 후보 49개 파일만 별도 디렉터리로 복사해 기존 .env·생성물·다른 저장소 없이 uv sync --frozen, checksum 폰트 설치, Ruff, pytest 37개, wheel/sdist 빌드를 확인했다. PostgreSQL과 설치된 Chromium은 로컬 실행 환경을 사용했다. wheel의 템플릿·Konva JS·라이선스 포함과 로컬 상태 제외를 확인했다. Markdown 상대 링크와 OpenAPI 코드 일치도 확인했다. 별도로 아래 GitHub CI 실행을 확인했다. Docker 이미지 실행을 대신하지 않는다.

## GitHub CI

커밋 b7a0521의 [Actions 실행](https://github.com/KT-Cloud-Tech-Up-team6/Fundit-AI-Funding-Story/actions/runs/35066056106)이 성공했다. Ubuntu의 PostgreSQL·Chromium·폰트 설치, Ruff, pytest, uv build를 통과했다. 최초 실행에서 확인된 Docker health 명령의 인용부호 오류를 수정 후 재실행한 결과다.
