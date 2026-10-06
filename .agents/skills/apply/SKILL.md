---
name: apply
description: Triggered by '/apply', '/action apply', or explicit requests to apply/execute the implementation plan created by the '/ask' skill. Reads the latest implementation plan and executes the planned code changes directly without needing phrases like '반영해줘'.
---

# 계획서 즉시 적용 스킬 (`apply`)

> [!IMPORTANT]
> **실행 목적 및 트리거 조건**
> 이 스킬은 `/ask` 스킬 등을 통해 작성된 `implementation_plan.md` 계획서가 있을 때, **사용자가 번거롭게 '반영해줘', '코드 적용해줘'를 쓰지 않고 `/apply` 명령어 하나로 계획서를 즉시 코드에 반영**하기 위한 실행 스킬입니다.

## 📌 주요 원칙 (Core Rules)

1. **계획서 기반 정확한 구현 (Plan-Driven Execution)**:
   - 가장 최근에 작성된 계획서(`implementation_plan.md` 또는 최근 질의응답에서 합의된 구현 계획)의 내용을 정확히 확인하고 순서대로 소스 코드를 수정합니다.
2. **터미널 고속 검증 준수 (Rule 2)**:
   - 코드 변경 후 브라우저나 스크린샷 검증을 띄우지 않고, 터미널 기반 정적 검증(`python -m py_compile`, `node -c` 등)을 즉시 수행합니다.
3. **자동 커밋 금지 준수 (Rule 3 & 4)**:
   - 코드 적용과 터미널 검증까지만 완료하고 대기합니다. Git 커밋 및 푸시는 사용자가 명시적으로 `/git-commit` 명령어를 입력할 때까지 실행하지 않습니다.

---

## 📋 수행 절차 (Workflow)

1. **최신 계획서 확인**:
   - 아티팩트 디렉터리의 `implementation_plan.md` 또는 직전 `/ask` 대화에서 수립된 계획 내용을 로드하여 변경 대상 파일 및 수정 항목을 파악합니다.
2. **코드 변경 단계별 적용**:
   - `replace_file_content`, `multi_replace_file_content`, `write_to_file` 등의 도구를 사용하여 계획서에 명시된 파일들을 차례대로 정확하게 수정합니다.
3. **터미널 정적 오류 검증**:
   - 수정된 언어에 맞추어 빠른 정적 검증을 실행합니다:
     - Python: `python -m py_compile <수정된 파일>`
     - JavaScript: `node -c <수정된 파일>`
4. **결과 보고**:
   - 반영된 파일 목록, 주요 변경 내용, 정적 검증 통과 결과를 사용자에게 명확하고 간결하게 보고합니다.
