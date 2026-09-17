# DB 연결 문서 대조 결과

검토일: 2026-09-17. 기준: [DB 연결 구조](https://app.notion.com/p/07e0c4485f2083adaed28191ef5d815a).

| 원본 요구사항 | 구현 및 확인 결과 |
| --- | --- |
| 서비스 전용 Postgres·고정 포트 | AI 전용 Compose, 원본 표와 동일한 5440, 다른 서비스 DB 생성 제거 |
| 공통 DB 환경변수·비밀값 제외 | DB_HOST/PORT/NAME/USERNAME/PASSWORD, .env 제외, 운영의 빈 접속값·로컬 예시값 거부 |
| 커넥션 풀 | psycopg runtime/checkpoint pool, API lifespan·worker process 생명주기 연결 |
| Flyway 버전 SQL·런타임 DDL 금지 | V1/V2, Compose 일회성 migration, 운영 배포 전 Job 및 validate 계약 |
| 실제 schema 불일치 시 기동 차단 | Flyway version과 필수 column/type/nullability/default 존재·PK/FK 존재·index·checkpoint version 검사, API/worker 기동 차단 및 readiness 실패 |
| domain port·persistence adapter | 순수 Python repository port, application service, infrastructure/persistence SQL·checkpointer, bootstrap 조립 |
| DB-per-service·애플리케이션 인가 | AI DB만 사용, project-service 소유권 검증 후 내부 토큰·프로젝트 범위로 호출 |
| CNPG·Secret | 공통 설정·TLS 전달·배포 순서 문서화. 실제 환경 검증은 DB 전달 후 WP-07에서 수행 |
| Testcontainers·CI 동일 경로 | 요청한 DB 통합 테스트에만 임시 PostgreSQL 생성, Flyway 적용, 초기화 실패도 정리 |

Python 서비스이므로 JPA/Hibernate/HikariCP는 도입하지 않는다. 승인된 계획대로 repository port·psycopg pool·schema 검사로 같은 책임을 구현한다. Spring의 기동 중 Flyway 대신 배포 선행 migration Job을 사용한다. 파일 checksum과 미적용 migration 검사는 Flyway validate 단계의 책임이며, 런타임 readiness가 checksum을 검사한다고 해석하지 않는다.

이번 재검토에서는 schema version만 보던 readiness를 실제 구조 검사로 보완하고 기동 실패에 연결했다. worker signal의 일반 예외가 무시될 수 있어 실패 시 process 종료로 처리한다. 운영 접속정보 누락, Flyway TLS 설정 누락, 테스트 migration 실패 시 정리 누락도 보완했다.

DB 변경만 포함한 커밋 대상으로 55개 테스트, Ruff, LangGraph migration drift, 패키지 빌드, Compose 설정을 검증한다. 별도 템플릿 변경이 있는 전체 작업 트리에서는 57개 테스트가 통과했다. 템플릿 변경은 이 DB 커밋에 포함하지 않는다.

실제 CNPG 연결·권한·연결 예산·BE 통합 검증은 미완료이며, DB 접속정보를 받은 후 진행한다. 로컬 통과를 운영 검증 완료로 표시하지 않는다.
