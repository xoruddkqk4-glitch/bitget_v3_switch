# `bitget_v3_switch` 일봉 기준점(어제 완성봉) 정상화 및 매수 로직 개선 구현 계획서

본 문서는 일봉 마감(매일 09:10~09:15 KST) 시점 평가 시, **당일 미완성 일봉(`df.iloc[-1]`)이 아닌 전일 완성 일봉(`df.iloc[-2]`)을 기준으로 진입/청산 조건을 정상 평가**하도록 지표 산출 및 신호 평가 로직을 바로잡는 상세 구현 계획서입니다.

> [!IMPORTANT]
> **모드 정책 준수 안내 (`/ask` 전용)**
> 본 문서는 `/ask` 규칙에 따라 작성된 계획서이며, **프로젝트 소스 코드를 아직 수정하지 않았습니다**.  
> 계획서를 검토하신 후 **`/apply`** 명령어를 입력하시면 본 계획서에 정의된 단계에 따라 실제 소스 코드 반영 및 터미널 정적 검증(`py_compile`)이 즉시 집행됩니다.

---

## 1. 문제 원인 상세 분석 (Issue Analysis)

### 1-1. 사용자 제기 현상
* "지금 매수 조건의 기준이 당일 일봉인 거 아니야? 어제 -10% 하락했는데도 매수가 일어나지 않았어."

### 1-2. 실제 비트겟 캔들 데이터 검증 (`SOXL/USDT:USDT` 1일봉)
```text
              datetime    open    high     low   close    등락률(종가/시가)  전일비(종가/전일종가)
[index 2]  2026-10-07  164.41  165.61  151.62  160.23      -2.54%             -2.54%
[index 3]  2026-10-08  160.23  161.44  136.57  142.40     -11.13% (폭락)     -11.13% (폭락)  <-- 어제 완성봉!
[index 4]  2026-10-09  142.40  151.32  141.65  150.65      +5.79% (반등)      +5.79% (반등)  <-- 오늘 실시간 진행 중
```

### 1-3. 기존 코드 결함 분석 (`strategy/indicator.py:29-45`)
* **기존 코드 로직:**
  ```python
  current_close = float(close_series.iloc[-1]) # 오늘 09:10 실시간가 (index 4)
  current_open = float(df['open'].iloc[-1])    # 오늘 09:00 시가 (index 4)
  prev_close = float(close_series.iloc[-2])    # 어제 종가 (index 3)

  candle_change = (current_close / current_open) - 1.0 # 오늘 10분간의 등락률
  daily_change = (current_close / prev_close) - 1.0   # 어제 종가 대비 오늘 10분간의 등락률
  ```
* **결과:**
  - 봇이 09:10에 실행되었을 때, 어제 하루 동안 형성되어 방금 마감된 **`index 3`(-11.13% 대폭락 봉)**을 평가한 것이 아니라, 오늘 09:00에 막 생성된 **`index 4`(오늘 10분짜리 봉)**의 시가 대비 등락률을 평가했습니다.
  - 오늘 아침 시점에는 가격이 반등세(+5.79%)였으므로, `candle_change`와 `daily_change` 모두 음봉 기준(-1.5%)과 급락 기준(-3.0%)에 도달하지 못해 진입 신호가 누락되었습니다.

### 1-4. 더미(Dummy) 메커니즘 동작 유의사항
* 신호가 정상 인식되더라도 `state.json` 상에서 `dummy_count`가 0인 경우:
  - 1회차 신호: **더미 1차 소진 (0원 매수)**
  - 2회차 신호: **더미 2차 소진 (0원 매수)**
  - 3회차 신호부터: **실제 코인 1.0 Unit 매수**
* 따라서 어제 신호가 정상 인식되었더라도 실제 주문이 아닌 '더미 1회차 소진'이 기록되는 것이 정상 동작이며, 현재는 신호 자체 판정이 누락되어 더미 소진조차 발생하지 않았던 것입니다.

---

## 2. 개선 및 수정 설계 (Solution Design)

### 2-1. 지표 산출 기준 분리 (`strategy/indicator.py`)
1. **신호 판정용 일봉 (어제 완성봉, `iloc[-2]`):**
   * `eval_open = df['open'].iloc[-2]` (어제 시가)
   * `eval_close = df['close'].iloc[-2]` (어제 종가)
   * `prev_eval_close = df['close'].iloc[-3]` (그저께 종가)
   * `candle_change = (eval_close / eval_open) - 1.0` (어제 음봉/양봉 등락률)
   * `daily_change = (eval_close / prev_eval_close) - 1.0` (어제 전일 대비 등락률)
   * `candle_date = df['datetime'].iloc[-2]` (판정 대상 완성봉 날짜 명시)
2. **이동평균선 (완성봉 기준 지표):**
   * `sma5 = sma5_series.iloc[-2]` (어제 마감 기준 5일선)
   * `sma60 = sma60_series.iloc[-2]` (어제 마감 기준 60일선)
   * `sma60_prev = sma60_series.iloc[-3]` (그저께 마감 기준 60일선)
   * `is_sma60_rising = sma60 > sma60_prev`
3. **주문 집행용 실시간 현재가 (`iloc[-1]`):**
   * `current_price = df['close'].iloc[-1]` (주문 체결 및 평가손익 계산용 실시간 현재가 유지)

### 2-2. 텔레그램 및 엑셀 로깅 메시지 명확화
* 텔레그램 일일 보고서에 **"평가 대상 완성 일봉: YYYY-MM-DD"**와 **"실시간 주문 기준 현재가: $XXX.XX"**를 명확히 구분 표시하여 사용자가 혼선 없이 신호 결과를 확인할 수 있도록 개선합니다.

---

## 3. 수정 대상 파일 및 변경 요약

| 파일 경로 | 수정 내용 |
| :--- | :--- |
| **`strategy/indicator.py`** | • `IndicatorCalculator.calculate()` 내 `candle_change`, `daily_change`, `sma5`, `sma60`, `candle_date` 산출 기준을 직전 완성봉(`iloc[-2]`)으로 변경<br/>• 실시간 집행용 `current_price`는 `iloc[-1]` 유지 |
| **`manager/trader.py`** | • 로그 및 엑셀 기록 시 평가 기준일(`indicators['candle_date']`)과 실시간 체결가 매핑 일관성 확인 |
| **`main.py`** | • 텔레그램 보고서의 일봉 기준일 및 현재가 표기 시 완성봉 기준 등락률 명확히 출력 |

---

## 4. 검증 계획 (Verification Plan)

1. **정적 문법 검증:**
   - `python -m py_compile strategy/indicator.py manager/trader.py main.py`
2. **실제 비트겟 캔들 단위 테스트:**
   - 최근 5일 캔들 데이터를 주입하여 어제(10월 8일) 캔들의 `candle_change = -11.13%`, `daily_change = -11.13%`가 정상 산출되고 롱 진입 신호가 올바르게 발생하는지 확인
3. **시뮬레이션 모의 실행:**
   - `python main.py --run-once`를 실행하여 롱 진입 신호 발생 및 더미 1차 소진(또는 실전 매수) 로그 출력 확인

---

## 5. 실행 대기 안내

* 본 계획서는 `/ask` 정책에 따라 작성되었으며, 자동으로 코드를 수정하지 않습니다.
* 위 계획대로 코드를 수정하고 적용하시려면 **`/apply`** 명령어를 입력해 주시기 바랍니다.
