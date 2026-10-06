# week_00-bitget_v3_switch

비트겟 자동매매 4기 - Week 00 Bitget V3 Switch 프로젝트

---

## 📌 프로젝트 소개
- 비트겟 거래소 V3 API 및 자동매매 전략 스위칭 시스템 (SOXL/USDT 헤지 모드 무손절 DCA 시스템)

---

## 📜 변경 및 업데이트 이력 (Cumulative Update History)
- 본 섹션은 `.agents/rules/rules.md` (Rule 4)에 따라 `/git-commit` 실행 시마다 최하단에 누적 기록됩니다.

## [2026-10-07 00:12] 업데이트 이력 (Commit ID: b1a2f34)
- **수정 내용**: 
  - `.agents` 에이전트 실행 규칙 및 커스텀 스킬(`apply`, `ask`, `git-commit`, `scratchpad`) 프로젝트 적용
  - 프로젝트 전역 실행 규칙 파일(`AGENTS.md`) 및 환경 보호 설정(`.gitignore`) 생성
  - 비트겟 V3 스위치 자동매매 시스템 요구사항 명세서(`bitget_v3_switch_prd.md`) 등록
  - GitHub 원격 저장소(`https://github.com/xoruddkqk4-glitch/bitget_v3_switch`) 연동 및 Git 초기화
- **검증 결과**:
  - Git remote 및 `main` 브랜치 설정 정상 검증 완료
  - `.gitignore` API 키 및 환경 파일 격리 규칙 검증 완료
  - 프로젝트 규칙 및 스킬 인식 상태 정상 확인

## [2026-10-07 00:30] 업데이트 이력 (Commit ID: 5d754bf)
- **수정 내용**: 
  - 계획서 파일 격리 보관 정책 수립 및 문서 재배치 (`docs/prd/bitget_v3_switch_prd.md`, `docs/implementation/implementation_plan.md`)
  - 에이전트 실행 규칙(`AGENTS.md`, `rules.md`, `ask/SKILL.md`, `apply/SKILL.md`)에 `docs/` 계획서 격리 원칙 반영
  - 비트겟 V3 스위치 일봉 헤지모드 무손절 DCA 자동매매 시스템 전체 모듈 구현:
    - `config.py` & `.env.example`: 10분할 동적 유닛, 2회 더미 방어, 1.25x 가중치, A/B 전략 파라미터, 모의매매 플래그
    - `utils/`: 일자별 파일 로테이션 로깅(`logger.py`), PRD 규격 엑셀 누적 기록기(`excel_logger.py`), 텔레그램 알림기(`notifier.py`)
    - `api/bitget_client.py`: ccxt 선물 래퍼 (Hedge Mode, Cross Margin, 1X 레버리지, 실시간 잔고/일봉 수집 및 모의 매매 지원)
    - `strategy/`: 추상 클래스(`base.py`), 지표 계산기(`indicator.py` - SMA5, SMA60 방향, 낙폭률/반등폭), Strategy A Long(`strategy_a_long.py`), Strategy B Short(`strategy_b_short.py`)
    - `manager/`: 원자적 상태 파일(`state.json`), 사이클 및 더미 제어(`cycle_manager.py`), 매도 우선 원칙 오케스트레이터(`trader.py`)
    - `main.py`: 일봉 마감(09:00 KST / 00:00 UTC) 단일 실행(`--run-once`) 및 상시 스케줄링(`--loop`) 엔트리 포인트
- **검증 결과**:
  - `python -m compileall -q .` 정적 구문 컴파일 검증 통과 (에러 0건)
  - `python main.py --run-once` 실전 모의 파이프라인 정상 구동 검증 (Bitget 실제 일봉 90개 수집, 지표 산출, 포지션 평가, 엑셀 및 상태 파일 기록 정상 확인)

