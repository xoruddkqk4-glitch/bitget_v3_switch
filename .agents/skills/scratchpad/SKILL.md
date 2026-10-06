---
name: scratchpad
description: Triggered strictly by '/scratchpad', '/action scratchpad', or explicit requests like 'scratchpad로 검증해줘'. Acts as an explicit exception to Rule 1 (Browser & Scratchpad Policy), allowing visual verification and browser subagent interaction when explicitly requested by the user.
---

# Scratchpad 시각적 검증 전용 스킬 (`scratchpad`)

> [!IMPORTANT]
> **실행 조건 (Strict Trigger Policy)**
> 이 스킬은 **오직 사용자가 `/scratchpad`, `/action scratchpad` 명령어를 명시적으로 입력하거나 "scratchpad로 확인/검증해줘"라고 직접 요청했을 때만 실행**됩니다.
> 기본 Rule 1 정책의 명시적 예외로서 작동하며, 평상시 일반 코드 수정 시에는 자동으로 구동되지 않습니다.

## 📌 주요 원칙 및 수행 절차 (Workflow)

1. **Rule 1 예외 적용**:
   - `/scratchpad` 명령 수신 시에 한해 `Rule 1. Browser & Scratchpad Policy`의 금지 조항을 유예하고 Scratchpad / Browser Subagent를 구동할 수 있습니다.

2. **브라우저 검증 및 시각적 확인**:
   - 브라우저 서브에이전트(`browser_subagent`)를 활용하여 대상 페이지/UI 요소를 직접 로드하고 버튼 클릭, 인터액티브 동작, 레이아웃을 시각적으로 확인합니다.

3. **검증 후 결론 및 보고**:
   - 시각적 검증 결과(인터액션 작동 여부, 버튼 반응, UI 이상 유무)를 사용자에게 종합하여 보고합니다.
