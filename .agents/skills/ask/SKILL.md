---
name: ask
description: Triggered by '/ask', '/action ask', or requests starting with '/ask'. Answers questions or creates an implementation plan WITHOUT modifying source code. Generated plans must NOT be automatically executed.
---

# 코드 수정 없는 질의응답 및 계획서 작성 전용 스킬 (`ask`)

`/ask`, `/action ask` 명령이나 질문/계획서 작성 전용 요청을 수신했을 때 실행되는 스킬입니다.

## 📌 핵심 원칙 (Core Rules)

1. **코드 수정 절대 금지 (No Code Modification)**:
   - 워크스페이스 내 프로젝트 소스 코드(모든 소스 파일, 설정 파일, 스크립트 등)를 절대로 수정하거나 삭제하지 않습니다.
   - 코드 조회/검색 도구(`view_file`, `grep_search`, `list_dir` 등)만 활용하여 분석 및 답변합니다.

2. **답변 또는 계획서 작성까지만 수행 (Answer or Plan Only)**:
   - 단순 질문인 경우 대화창 답변으로 완결합니다.
   - 기술적 구현/복잡한 코드 변경 작업 요청인 경우 `implementation_plan.md` 아티팩트 작성까지만 진행합니다.

3. **계획서 자동 실행 절대 금지 (Never Auto-Execute Plan)**:
   - `/ask` 모드로 작성된 계획서는 시스템 정책 등에 의해 자동 승인(Auto-Approve/Proceed) 되더라도 **절대로 자동으로 코드를 수정/실행해서는 안 됩니다**.
   - 계획서 작성 후 반드시 사용자의 **명시적 추가 명령(`/apply`, "반영해줘" 등)**이 있을 때까지 대기합니다.

## 📋 수행 절차 (Workflow)

1. **요청 분석 및 기존 코드 분석**:
   - 사용자 질의를 확인하고 필요한 파일 및 구조를 조회하여 분석합니다.
2. **응답 구분**:
   - **단순 질의**: 코드 수정 없이 질문에 정확하고 명쾌하게 답변합니다.
   - **복잡한 작업/구현 요청**: `implementation_plan.md` 아티팩트에 분석 결과와 구현 계획을 작성합니다.
3. **완료 및 사용자 승인 대기**:
   - 답변 또는 계획서 링크를 사용자에게 제공하고, 사용자의 명시적인 추가 실행 요청(`/apply` 등) 전까지 작업을 정지하고 대기합니다.
