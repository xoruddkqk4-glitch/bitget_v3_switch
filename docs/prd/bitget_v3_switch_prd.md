# `bitget_v3_switch` 자동매매 시스템 최종 구현 계획서 (PRD)

---

## 1. 시스템 개요 (Overview)

* **프로젝트명:** `bitget_v3_switch`
* **대상 거래소 및 종목:** Bitget 선물 마켓 — `SOXL/USDT:USDT` (미국 반도체 3배 레버리지 ETF 추종 토큰/선물)
* **거래 환경 설정:**
  * **레버리지:** 1X (1배율)
  * **마진 모드:** Cross (교차 마진)
  * **포지션 모드:** Hedge Mode (롱/숏 독립 동시 보유)
* **주요 타임프레임:** 일봉 (Daily Candle, 매일 일봉 마감 시점 1회 스케줄링 실행)
* **핵심 운용 컨셉:**
  * **A 전략 (Long DCA):** 하락 조건 발생 시 더미 2회 소진 후 동적 분할 매수 $\rightarrow$ 60일선 조건부 독립 익절
  * **B 전략 (Short DCA):** 상승 조건 발생 시 더미 2회 소진 후 동적 분할 숏 진입 $\rightarrow$ 60일선 대칭 조건부 독립 익절
  * **Hedge Mode 무손절 독립 운용:** 강제 스위칭 손절 없이, A/B 각 전략이 자신의 청산 목표가에 도달할 때까지 독립적으로 버텨서 익절 탈출

---

## 2. 자금 관리 및 더미(Dummy) 메커니즘 명세

1. **동적 1.0 Unit 산출 수식:**
   $$\text{Current Unit Size (\$)} = \frac{\text{현재 계좌 총 평가 잔고 (Free Margin + Position Margin)}}{10}$$
   * 매 진입 시점마다 실시간 총 평가 잔고를 수집하여 1 Unit의 기본 $ 금액을 동적으로 재계산합니다.

2. **유닛 가중치 (더미 미포함 실제 매수 회차 기준):**
   * **더미 1~2회차:** $\$0$ (0원 매수, 더미 소진만 진행)
   * **실제 매수 1~4회차 (`executed_units`: 0~3):** $1.0 \times \text{Unit Size}$ (기본 1 유닛)
   * **실제 매수 5회차 이상 (`executed_units` $\ge 4$):** $1.25 \times \text{Unit Size}$ (비중 확대 유닛 적용)

3. **더미(Dummy) 방어 시스템:**
   * 새 싸이클 시작 후 진입 조건이 충족되더라도 **1~2회차 신호는 0원 매수(더미 소진)** 처리합니다.
   * **3회차 신호부터 실제 1.0 Unit 주문**을 집행하여, 하락/상승 초입 물리거나 뇌동매매되는 현상을 구조적으로 방지합니다.

4. **매도 우선 원칙:**
   * 당일 매도(청산)와 매수 신호가 동시 발생할 경우 **매도를 우선 실행**합니다.
   * 청산 완료 당일 발생한 매수 신호는 **신규 싸이클의 1차 더미**로 이관 처리합니다.

---

## 3. A 전략 및 B 전략 세부 매매 조건

### **A 전략: Long DCA (`strategy/strategy_a_long.py`)**
1. **진입 조건 (OR):**
   * 음봉 $\le -1.5\%$ ($\text{종가}/\text{시가} - 1 \le -0.015$)
   * 전일 대비 등락률 $\le -3.0\%$ ($\text{종가}/\text{전일종가} - 1 \le -0.030$)
2. **청산 목표가 산출:**
   * **60일선 상승 중 ($SMA_{60} > SMA_{60, prev}$):** Target = $SMA_5 \times 1.02$ 이상 다다를 때 롱 전량 청산
   * **60일선 하락 & 낙폭률 $> -30\%$:** Target = $SMA_5 \times 0.99$ 이하 다다를 때 롱 전량 청산 (빠른 본전 탈출)
   * **60일선 하락 & 낙폭률 $\le -30\%$:** Target = $SMA_5 \times 1.03$ 이상 다다를 때 롱 전량 청산 (깊은 하락 후 반등 익절)
   * *(※ 낙폭률 = $(\text{현재가} - \text{싸이클 내 전고점}) / \text{싸이클 내 전고점}$)*

---

### **B 전략: Short DCA (`strategy/strategy_b_short.py`)**
1. **진입 조건 (OR):**
   * 양봉 $\ge +1.5\%$ ($\text{종가}/\text{시가} - 1 \ge +0.015$)
   * 전일 대비 등락률 $\ge +3.0\%$ ($\text{종가}/\text{전일종가} - 1 \ge +0.030$)
2. **청산 목표가 산출:**
   * **60일선 상승 중 ($SMA_{60} > SMA_{60, prev}$):** Target = $SMA_5 \times 0.98$ 이하 다다를 때 숏 전량 청산
   * **60일선 하락 & 반등폭 $< +30\%$:** Target = $SMA_5 \times 1.01$ 이하 다다를 때 숏 전량 청산 (본전 탈출)
   * **60일선 하락 & 반등폭 $\ge +30\%$:** Target = $SMA_5 \times 0.97$ 이하 다다를 때 숏 전량 청산 (깊은 반등 후 눌림 익절)
   * *(※ 반등폭 = $(\text{현재가} - \text{싸이클 내 전저점}) / \text{싸이클 내 전저점}$)*

---

## 4. 프로젝트 패키지 디렉토리 구조 (Package Structure)

```text
bitget_v3_switch/
├── main.py                     # 일봉 마감 시점 통합 실행 파이프라인 엔트리 포인트
├── config.py                   # API 키, 파라미터(10등분, -1.5%, -3%, 60일선 배수 등) 설정
├── state.json                  # A/B 전략 각각의 더미/실제유닛 카운트, 평단가 영속성 저장 파일
├── trade_history.xlsx          # openpyxl 기반 매매/더미 내역 누적 기록 엑셀 파일 (자동 생성)
│
├── strategy/                  
│   ├── __init__.py
│   ├── base.py                # 전략 기본 추상 클래스 (BaseStrategy)
│   ├── indicator.py           # 5일선, 60일선 방향, 전고점/전저점, 낙폭률/반등폭 지표 계산기
│   ├── strategy_a_long.py     # StrategyALong 클래스 (Long DCA 조건 판단)
│   └── strategy_b_short.py    # StrategyBShort 클래스 (Short DCA 조건 판단)
│
├── manager/                   
│   ├── __init__.py
│   ├── trader.py              # Bitget Hedge Mode 주문(Long/Short 분리 진입/청산) 집행 모듈
│   └── cycle_manager.py       # A/B 전략별 더미 카운트 및 실제 매수 횟수(executed_units) 제어
│
├── api/                       
│   ├── __init__.py
│   └── bitget_client.py       # ccxt 기반 Bitget 선물 API Wrapper (Hedge Mode 강제 및 OHLCV 수집)
│
└── utils/                     
    ├── __init__.py
    ├── logger.py              # 파일 및 콘솔 Logging 설정
    ├── excel_logger.py        # openpyxl 기반 trade_history.xlsx 기록 모듈
    └── notifier.py            # 텔레그램 상태/매매 알림 전송기
```

---

## 5. 데이터 영속성 및 로깅 명세 (Data & Logging Schemas)

### **① `state.json` 영속성 스키마**
더미 카운트(`dummy_count`)와 실제 집행된 매수 횟수(`executed_units`)를 엄격하게 분리하여 영속 관리합니다.

```json
{
  "strategy_A": {
    "cycle_id": 1,
    "dummy_count": 0,
    "executed_units": 0,
    "avg_price": 0.0,
    "total_qty": 0.0
  },
  "strategy_B": {
    "cycle_id": 1,
    "dummy_count": 0,
    "executed_units": 0,
    "avg_price": 0.0,
    "total_qty": 0.0
  },
  "last_updated": "2026-10-07 00:00:00"
}
```

### **② `trade_history.xlsx` 엑셀 기록 스키마**
매매 집행(BUY_UNIT, SELL_CLEAR) 또는 더미 소진(BUY_DUMMY) 이벤트 발생 시 자동 기록됩니다.

| 날짜/시간 | 전략 구분 | 이벤트 유형 | 체결가 ($) | 수량 (Qty) | 유닛 번호 | 더미 소진 현황 | 총 평가 잔고 ($) | 비고 |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 2026-10-07 00:00 | strategy_A | BUY_DUMMY | - | - | - | 1 / 2 | $10,000 | 음봉 -1.8% 더미 소진 |
| 2026-10-08 00:00 | strategy_A | BUY_UNIT | $25.40 | 39.37 | 실제 1회차 (1.0x) | 2 / 2 | $10,000 | 1.0 Unit 실제 매수 |
| 2026-10-12 00:00 | strategy_A | SELL_CLEAR | $27.10 | 39.37 | ALL_CLEAR | 0 / 2 | $10,210 | 5일선 * 1.02 익절 청산 |

---

## 6. 메인 실행 파이프라인 처리 흐름 (`main.py`)

일봉 마감 시점 마다 매일 1회 실행되는 통합 제어 파이프라인입니다.

1. **데이터 수집:** Bitget에서 `SOXL/USDT:USDT` 최근 100일 분 일봉 수집 및 Hedge Mode 설정 재확인.
2. **지표 산출:** `IndicatorCalculator`를 통해 $SMA_5$, $SMA_{60}$, $SMA_{60, prev}$, 낙폭률/반등폭 산출.
3. **A 전략 (Long) 파이프라인:**
   * A 전략 청산 조건 검사 $\rightarrow$ 충족 시 롱 포지션 전량 청산, `state.json` 초기화 및 엑셀 기록.
   * A 전략 매수 조건 검사 $\rightarrow$ 충족 시 (당일 청산 발생 시 1차 더미로 이관 / 더미 $<2$ 이면 더미 소진 / 더미 $=2$ 이면 `executed_units`에 따라 1.0배 또는 1.25배 실전 매수 집행).
4. **B 전략 (Short) 파이프라인:**
   * B 전략 청산 조건 검사 $\rightarrow$ 충족 시 숏 포지션 전량 청산, `state.json` 초기화 및 엑셀 기록.
   * B 전략 매수 조건 검사 $\rightarrow$ 충족 시 (당일 청산 발생 시 1차 더미로 이관 / 더미 $<2$ 이면 더미 소진 / 더미 $=2$ 이면 `executed_units`에 따라 1.0배 또는 1.25배 실전 숏 매도 집행).
5. **알림 전송:** 텔레그램으로 당일 수행 결과 요약 전송.

---

## 🤖 Antigravity 요청 프롬프트 예시

> "첨부한 `bitget_v3_switch` 최종 구현 계획서(PRD) 문서 내용에 맞추어 프로젝트 디렉토리 구조를 생성하고, 전체 모듈(`config.py`, `state.json`, `strategy/`, `manager/`, `api/`, `utils/`, `main.py`) 파이썬 코드를 순차적으로 구현해 줘."