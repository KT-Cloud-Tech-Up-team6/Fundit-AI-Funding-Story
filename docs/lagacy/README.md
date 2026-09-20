# 과거 문서 보관

이 폴더의 문서는 현재 Funding Story AI 계약이나 운영 기준이 아니다.
2026-09-17 DB 연결 정합화 작업의 이력과 검토 근거가 필요할 때만 참고한다.

현재 기준은 다음 문서를 따른다.

- [실행·API 계약](../architecture.md)
- [개발 환경과 실행](../development.md)
- [검증 범위](../validation.md)

특히 Funding Story는 AI PostgreSQL 영구 상태와 LangGraph 영구 checkpoint를 사용하지 않으며,
추가 DB migration·ERD는 만들지 않는다. 과거 문서의 DB·checkpoint·export 계획을 현재 구현이나
팀 간 계약으로 재사용하지 않는다.
