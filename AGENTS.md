# Agent Execution Rules: Terminal-Only Fast Verification

에이전트 실행 규칙: 터미널 전용 고속 검증 (`.agents/rules/rules.md`)

## 1. Browser & Scratchpad Policy (브라우저 및 시각 검증 정책)
- **STRICT REQUIREMENT:** Do NOT launch Chrome, Scratchpad, or any browser instance for visual verification during standard code editing tasks.
- **NO AUTOMATIC SCREENSHOTS:** Never take screenshots or perform visual inspection automatically after editing code.
- Trust Hot Module Replacement (HMR) on the user's browser for UI updates.
- **EXCEPTIONS (명시적 예외 조건):** 오직 사용자가 `/scratchpad`, `/action scratchpad` 명령어를 명시적으로 입력하거나 "scratchpad로 검증해줘"라고 직접 요청한 경우에 한해 본 정책의 예외가 적용되어 Scratchpad / 브라우저 시각 검증 도구를 구동할 수 있습니다.

## 2. Terminal-Based Error Verification (터미널 기반 정적 오류 검증)
- After making code changes, perform quick static verification via terminal commands instead of browser checks.
- Runs fast type checking or linting depending on the project setup:
  - TypeScript project: Run `npx tsc --noEmit` (or `npm run type-check`)
  - Next.js / React project: Run `npm run lint`
  - Vanilla JS / Node.js project: Run `node -c app.js` (또는 `node --check <file>.js`)
  - Python project: Run `python -m py_compile <file>.py`
- If non-critical lint warnings occur, do not get stuck in an endless fixing loop; report them briefly and conclude.
- Do NOT run heavy dev servers, build commands (`npm run build`), or long-running test suites unless explicitly requested.

## 3. Workflow Optimization (일반 수정 모드)
- **일반적 코드 수정/오류 복구 작업 시:**
  - 코드 변경을 적용하고 빠른 터미널 오류 검증(타입 체크/구문 검사)만 수행한 후 즉시 작업을 종료합니다.
  - **절대로 자동으로 Git 커밋(`git commit`) 및 푸시(`git push`)를 수행하지 않습니다.**
- 사용자가 명시적으로 `/git-commit` 명령어를 입력할 때까지 커밋/푸시 없이 대기합니다.

## 4. Git 커밋 및 푸시 정책 (오직 `/git-commit` 명령어 수신 시에만 실행)
- **엄격 조건:** 오직 사용자로부터 `/action git-commit`, `/git-commit`, 또는 "README 업데이트 후 푸시"와 같은 **명시적인 커밋/푸시 명령어**를 수신했을 때만 Git 커밋 및 푸시 워크플로우를 수행합니다.
- **일반 수정 시 자동 커밋 금지:** 기능 구현, 버그 수정, 코드 리팩토링 등 일반적인 요청 시에는 코드 수정과 터미널 오류 검증만 진행하고 커밋/푸시는 절대로 자동으로 실행하지 않습니다.
- **`/git-commit` 수신 시 수행 절차:**
  1. 작업 내용 및 검증 결과를 정리하여 `README.md` 하단에 누적 이력을 작성합니다.
  2. 상세한 커밋 메시지와 함께 `git add .` 및 `git commit`을 진행합니다.
  3. GitHub 원격 저장소로 `git push`를 수행하고 결과를 사용자에게 종합 보고합니다.
- `README.md` 업데이트 시 코드 수정 사항, 새로 구현된 기능, 발생한 문제 및 해결 과정, 검증 결과를 명확하게 기록해야 합니다.
- **`README.md` 변경 이력 누적 기록 규칙:**
  - `README.md` 파일을 수정할 때, 업데이트되는 내용을 `README.md` 파일의 맨 뒷부분(하단)에 **날짜 및 시간(서울 기준 시각: YYYY-MM-DD HH:mm)** 기준으로 누적하여 기록해야 합니다.
  - 누적 기록에는 **날짜 및 시간(Date & Time, 서울 기준 KST)**, **커밋 ID(Commit Hash)**, **수정 내용(Modification Details)**이 반드시 포함되어야 합니다.
  - `README.md`의 기존 본문 내용을 수정하는 것도 허용되며, 변경 이력은 맨 뒷부분에 지속해서 누적됩니다.
- 커밋 메시지는 한국어로 작성하며, `docs: update README.md and detailed commit results` 포맷을 따릅니다.

## 5. `/ask` 질의응답 및 계획 전용 모드 정책 (No Code Modification & No Auto-Execution)
- 사용자로부터 `/ask`, `/action ask`, 또는 `/ask`로 시작하는 질의를 받으면 프로젝트 소스 코드를 절대로 수정하지 않아야 합니다.
- 단순 질문인 경우 대화 답변만 수행하고, 기술적/복잡한 변경 요청인 경우 계획서 작성까지만 진행합니다.
- **계획서 폴더 격리 보관 원칙:** 모든 계획서 파일(PRD, 구현 계획서 등)은 프로젝트 루트에 두지 않고 `docs/` 하위에 각 계획서별 전용 폴더(예: `docs/prd/`, `docs/implementation/implementation_plan.md`)를 생성하여 저장합니다.
- **계획서 자동 실행 금지:** `/ask` 모드로 작성된 계획서는 시스템 자동 승인(Auto-Approve/Proceed)이 전달되더라도 절대로 자동으로 코드를 변경해서는 안 되며, 사용자의 명시적인 추가 대화 명령이 있을 때까지 대기해야 합니다.

## 6. `/scratchpad` 브라우저 검증 전용 모드 정책 (Visual Verification Explicit Exception)
- 사용자로부터 `/scratchpad`, `/action scratchpad`, 또는 "scratchpad로 검증해줘"라는 명시적인 요청을 수신하는 경우, Rule 1 정책의 명시적 예외를 적용합니다.
- Scratchpad 및 브라우저 검증 도구(`browser_subagent`)를 활용하여 대상 페이지 및 UI 인터액션 기능을 시각적으로 직접 확인하고 결과를 사용자에게 보고합니다.

## 7. `/apply` 계획서 즉시 적용 모드 정책 (Plan Execution Mode)
- 사용자로부터 `/apply`, `/action apply` 명령을 수신하면, `docs/implementation/implementation_plan.md` (또는 아티팩트) 계획서의 내용을 확인하고 소스 코드에 즉시 단계별로 반영합니다.
- '반영해줘' 등의 자연어 승인 문구 없이도 `/apply` 명령어 하나로 즉시 구현을 시작합니다.
- 코드 수정 후에는 Rule 2에 따라 빠른 터미널 정적 검증을 수행하고 결과를 보고합니다.
- 단, Rule 3/4에 따라 자동 커밋 및 푸시는 진행하지 않으며 명시적인 `/git-commit` 수신 시에만 커밋/푸시를 수행합니다.
