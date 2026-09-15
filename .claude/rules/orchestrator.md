---
paths:
  - "src/orchestrator/**/*.py"
---
# Orchestrator / LangGraph 컨벤션
- State 스키마는 TypedDict + Annotated reducer로 정의
- 노드 함수는 부작용 없이 State를 받아 State를 반환하는 순수 함수 형태 유지
