# week_00-bitget_v3_switch

비트겟 선물 V3 API 기반 **SOXL/USDT 무손절 롱/숏 DCA 헤지모드 자동매매 시스템**

---

## 📌 1. 시스템 개요 (System Overview)

* **대상 거래소 및 종목:** Bitget USDT 선물 마켓 — `SOXL/USDT:USDT`
* **거래 환경 설정:**
  * **레버리지:** 1X (1배율 저위험 운용)
  * **마진 모드:** Cross (교차 마진)
  * **포지션 모드:** Hedge Mode (롱/숏 독립 동시 보유)
* **타임프레임:** 일봉 (Daily Candle, 매일 00:00 UTC / 09:00 KST 일봉 마감 시점 1회 스케줄링 실행)
* **핵심 운용 컨셉:**
  * **A 전략 (Long DCA):** 음봉/급락 시 2회 더미 소진 후 동적 분할 매수 $\rightarrow$ 60일선 조건부 독립 익절
  * **B 전략 (Short DCA):** 양봉/급등 시 2회 더미 소진 후 동적 분할 숏 진입 $\rightarrow$ 60일선 대칭 조건부 독립 익절
  * **Hedge Mode 무손절 독립 운용:** 강제 스위칭 손절 없이, A/B 각 전략이 자신의 청산 목표가에 도달할 때까지 독립적으로 버텨서 익절 탈출

---

## 🛡️ 2. 자금 관리 및 더미(Dummy) 메커니즘

1. **동적 1.0 Unit 산출 공식:**
   $$\text{Current Unit Size (\$)} = \frac{\text{현재 계좌 총 평가 잔고 (Free Margin + Position Margin)}}{10}$$
   * 매 진입 시점마다 실시간 총 평가 잔고를 수집하여 1 Unit의 기본 $ 금액을 동적으로 재계산합니다.

2. **유닛 가중치 (실제 매수 회차 기준):**
   * **더미 1~2회차:** $\$0$ (0원 매수, 더미 카운트만 소진)
   * **실제 매수 1~4회차 (`executed_units`: 0~3):** $1.0 \times \text{Unit Size}$ (기본 1 유닛)
   * **실제 매수 5회차 이상 (`executed_units` $\ge 4$):** $1.25 \times \text{Unit Size}$ (비중 확대 유닛 적용)

3. **더미(Dummy) 방어 시스템:**
   * 새 싸이클 시작 후 진입 조건이 충족되더라도 **1~2회차 신호는 0원 매수(더미 소진)** 처리합니다.
   * **3회차 신호부터 실제 1.0 Unit 주문**을 집행하여, 초입 물리거나 뇌동매매되는 현상을 구조적으로 방지합니다.

4. **매도 우선 원칙:**
   * 당일 매도(청산)와 매수 신호가 동시 발생할 경우 **매도를 우선 실행**합니다.
   * 청산 완료 당일 발생한 매수 신호는 **신규 싸이클의 1차 더미**로 이관 처리합니다.

---

## 📈 3. 전략별 세부 매매 조건

### **A 전략: Long DCA (`strategy/strategy_a_long.py`)**
1. **진입 조건 (OR):**
   * 당일 음봉 $\le -1.5\%$ ($\text{종가}/\text{시가} - 1 \le -0.015$)
   * 전일 대비 등락률 $\le -3.0\%$ ($\text{종가}/\text{전일종가} - 1 \le -0.030$)
2. **청산 목표가 산출:**
   * **60일선 상승 중 ($SMA_{60} > SMA_{60, prev}$):** Target = $SMA_5 \times 1.02$ 이상 다다를 때 롱 전량 청산
   * **60일선 하락 & 낙폭률 $> -30\%$:** Target = $SMA_5 \times 0.99$ 이하 다다를 때 롱 전량 청산 (본전 탈출)
   * **60일선 하락 & 낙폭률 $\le -30\%$:** Target = $SMA_5 \times 1.03$ 이상 다다를 때 롱 전량 청산 (과낙폭 반등 익절)
   * *(※ 낙폭률 = $(\text{현재가} - \text{싸이클 내 전고점}) / \text{싸이클 내 전고점}$)*

---

### **B 전략: Short DCA (`strategy/strategy_b_short.py`)**
1. **진입 조건 (OR):**
   * 당일 양봉 $\ge +1.5\%$ ($\text{종가}/\text{시가} - 1 \ge +0.015$)
   * 전일 대비 등락률 $\ge +3.0\%$ ($\text{종가}/\text{전일종가} - 1 \ge +0.030$)
2. **청산 목표가 산출:**
   * **60일선 상승 중 ($SMA_{60} > SMA_{60, prev}$):** Target = $SMA_5 \times 0.98$ 이하 다다를 때 숏 전량 청산
   * **60일선 하락 & 반등폭 $< +30\%$:** Target = $SMA_5 \times 1.01$ 이하 다다를 때 숏 전량 청산 (본전 탈출)
   * **60일선 하락 & 반등폭 $\ge +30\%$:** Target = $SMA_5 \times 0.97$ 이하 다다를 때 숏 전량 청산 (과반등 눌림 익절)
   * *(※ 반등폭 = $(\text{현재가} - \text{싸이클 내 전저점}) / \text{싸이클 내 전저점}$)*

---

## 🗂️ 4. 프로젝트 패키지 구조 (Package Structure)

```text
week_00-bitget_v3_switch/
├── docs/                                  # 계획서 및 명세서 전용 격리 폴더
│   ├── prd/
│   │   └── bitget_v3_switch_prd.md         # 요구사항 명세서 (PRD)
│   └── implementation/
│       └── implementation_plan.md         # 아키텍처 및 상세 구현 계획서
│
├── .agents/                               # 에이전트 실행 규칙 및 특화 스킬 세트
│   ├── rules/
│   │   └── rules.md                       # 터미널 고속 검증 및 커밋 정책
│   └── skills/
│       ├── ask/SKILL.md                   # /ask (코드 수정 없는 질의/계획 모드)
│       ├── apply/SKILL.md                 # /apply (계획서 소스 코드 즉시 반영 모드)
│       ├── git-commit/SKILL.md            # /git-commit (README 이력 갱신 및 커밋/푸시)
│       └── scratchpad/SKILL.md            # /scratchpad (브라우저 시각 검증 모드)
│
├── api/                                   # 거래소 API 연동 계층
│   ├── __init__.py
│   └── bitget_client.py                   # ccxt 선물 래퍼 (Hedge Mode, 잔고, OHLCV, 모의매매)
│
├── strategy/                              # 지표 계산 및 A/B 전략 로직
│   ├── __init__.py
│   ├── base.py                            # 전략 기본 추상 클래스 (BaseStrategy)
│   ├── indicator.py                       # SMA5, SMA60, 낙폭률/반등폭 산출 모듈
│   ├── strategy_a_long.py                 # Strategy A (Long DCA 조건 판단)
│   └── strategy_b_short.py                # Strategy B (Short DCA 조건 판단)
│
├── manager/                               # 사이클 영속성 및 주문 제어 계층
│   ├── __init__.py
│   ├── cycle_manager.py                   # 더미 및 가중 유닛, 원자적 state.json 제어
│   └── trader.py                          # 매도 우선 원칙 및 주문 집행 통합 오케스트레이터
│
├── utils/                                 # 공용 유틸리티 계층
│   ├── __init__.py
│   ├── logger.py                          # 10MB 자동 로테이팅 및 콘솔 로거
│   ├── excel_logger.py                    # trade_history.xlsx 누적 기록기
│   └── notifier.py                        # 텔레그램 일일 보고 및 에러 알림기
│
├── config.py                              # 시스템 전역 파라미터 및 환경 설정
├── state.json                             # A/B 전략 사이클, 더미, 평단가 영속 파일
├── trade_history.xlsx                     # 거래/더미 이력 엑셀 파일 (자동 생성)
├── main.py                                # 일봉 마감 실행 파이프라인 엔트리 포인트
├── AGENTS.md                              # 프로젝트 루트 상시 규칙 파일
├── .env.example                           # 환경변수 템플릿
├── .gitignore                             # 보안 파일 및 캐시 무시 설정
└── README.md                              # 프로젝트 종합 문서 및 누적 업데이트 이력
```

---

## 🚀 5. 실행 방법 (Usage)

### 1) 환경 설정 (`.env`)
`.env.example` 파일을 복사하여 `.env`를 생성하고 실제 API 키와 텔레그램 토큰을 설정합니다:
```bash
cp .env.example .env
```
*(※ `PAPER_TRADING=true` 설정 시 실제 주문 없이 가상 잔고로 안전하게 파이프라인을 테스트할 수 있습니다.)*

### 2) 단일 즉시 실행 (테스트 및 작업 스케줄러 연동용)
```bash
python main.py --run-once
```

### 3) 상시 백그라운드 스케줄링 실행
```bash
python main.py --loop
```
* 매일 일봉 마감 시점(00:00:15 UTC / 09:00:15 KST)까지 자동으로 대기하며 정시마다 1회 파이프라인을 반복 실행합니다.

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

## [2026-10-07 00:33] 업데이트 이력 (Commit ID: 1621b0d)
- **수정 내용**: 
  - `README.md` 문서 전면 개편: 계획서(PRD 및 구현 계획서) 기반 시스템 개요, 자금 관리 및 더미 방어 메커니즘, A/B 전략 매매 조건, 상세 패키지 디렉터리 구조 및 실행 가이드 추가
- **검증 결과**:
  - 문서 마크다운 렌더링 및 디렉터리/파일 경로 일치 검증 완료
