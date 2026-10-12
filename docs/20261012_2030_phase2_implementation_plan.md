# Phase 2 실행 계획서 — 전략·자금 규칙 개선 (LLM 즉시 실행용)

- 작성 일시: 2026-10-12 20:30 (KST)
- 근거 문서: [v2 계획서](./20261012_2013_code_review_implementation_plan_v2.md) Phase 2 (2-1 ~ 2-4)
- 선행 문서: [Phase 1](./20261012_2030_phase1_implementation_plan.md) (**Phase 1 적용·검증 완료 후에만 실행**)
- 후속 문서: [Phase 3](./20261012_2030_phase3_implementation_plan.md)
- 기준 코드: **Phase 1 적용 후** 워킹트리. 아래 Diff는 모두 Phase 1 결과 코드 기준이며, scratch 복사본에 적용해 `py_compile` + 시나리오 검증을 통과한 내용입니다.

---

## 0. 실행 규칙 (실행 LLM 필독)

1. **Phase 1이 적용되지 않았다면 중단**합니다. 확인 방법: `api/exceptions.py`, `manager/reconciler.py`가 존재하고 `manager/trader.py`에 `SIDE_CFG`가 정의되어 있어야 합니다.
2. **Step 순서대로** 적용합니다. Step 1(config)의 상수를 뒤 Step들이 import 합니다.
3. 모든 변경은 **Diff**입니다. Diff의 `-` 줄을 찾아 `+` 줄로 바꾸고, 앞에 공백이 있는 줄(문맥 줄)은 그대로 둡니다.
4. 줄 번호는 **Phase 1 적용 후 기준 근사값**입니다. 위치는 반드시 함께 제시된 **앵커(정확한 기존 코드)** 로 찾습니다. 앵커가 파일에 정확히 1회 존재하지 않으면 중단하고 보고합니다.
5. 계획서에 없는 리팩토링·포맷팅·주석 삭제를 하지 않습니다. Git 커밋/푸시 금지 (AGENTS.md Rule 3/4).
6. 실행 셸은 Windows PowerShell, 작업 디렉터리는 프로젝트 루트(`week_00-bitget_v3_switch`)입니다.

## 0-1. 기본 결정값 (사용자 미확정 항목 — 설정 상수로 노출)

| 항목 | 기본값 | 위치 |
| :-- | :-- | :-- |
| 1.0 Unit 분모 | `UNIT_DIVISOR = 12.0` | `config.py` |
| 싸이클당 실제 매수 최대 회차 | `MAX_EXECUTED_UNITS = 10` | `config.py` |
| 주문금액 가용 잔고 상한 비율 | `MARGIN_SAFETY_RATIO = 0.95` | `config.py` |
| 롱 본전탈출 본전 가드 (선택) | `BREAKEVEN_GUARD_ENABLED = False` | `config.py` |
| 본전 가드 최소 수익률 | `BREAKEVEN_GUARD_MIN_PCT = 0.002` | `config.py` |
| 2단계 4H 5MA 판정 방식 | `STAGE2_SIGNAL_MODE = "closed_candle"` | `config.py` |

## 0-2. 변경 파일 및 의존 순서

| Step | 파일 | 방식 | 의존 | 관련 항목 |
| :-- | :-- | :-- | :-- | :-- |
| 1 | `config.py` | Diff | — | 2-1, 2-2, 2-4 |
| 2 | `strategy/strategy_a_long.py` | Diff | Step 1 | 2-1, 2-4 |
| 3 | `strategy/strategy_b_short.py` | Diff | Step 1 | 2-4 |
| 4 | `strategy/indicator.py` | Diff | Step 1 | 2-4 |
| 5 | `utils/formatters.py` | Diff | Step 1 | 2-2 |
| 6 | `manager/cycle_manager.py` | Diff | Step 1 | 2-2, 2-3 |
| 7 | `utils/excel_logger.py` | Diff | — | 2-3 |
| 8 | `manager/trader.py` | Diff | Step 1~7 | 2-2, 2-3, 2-4 |
| 9 | `main.py` | Diff | Step 4 | 2-4 |
| 10 | `docs/prd/bitget_v3_switch_prd.md`, `README.md` | Diff (문서) | — | 전체 |
| 11 | 통합 검증 | — | 전체 | — |

**동작 변경 요약** (Phase 1 대비):
- ① 롱 "완만한 하락장 본전탈출"을 `현재가 >= SMA5×0.99`(반등 회복 시 탈출)로 변경.
- ② 1.0 Unit = 잔고/12. 최대 회차 10은 별도 상수로 분리. 1회 주문금액은 `가용 잔고 × 레버리지 × 0.95`를 넘지 않음.
- ③ 트레일링 중 일봉 재평가는 보존선을 **유리한 방향으로만** 갱신(롱 상향/숏 하향). 즉시청산·미충족 결과는 무시.
- ④ 4H 추세 이탈은 **마감된 4H 완성봉** 기준이며, 트레일링 돌입 이후 마감된 봉만 인정. 보존선은 5분마다 실시간가로 즉시 판정.

---

## Step 1. `config.py` (Diff)

### (1) 파일 경로
`config.py`

### (2) 기존 코드 영역
- 1-A: L47 `PRESERVATION_BUFFER_PCT` 줄 바로 아래 (섹션 2 스케줄링 블록)
- 1-B: 약 L63 `UNIT_DIVISOR` 줄 (섹션 3 자금 관리)
- 1-C: 약 L83 `LONG_EXIT_FALLING_SMA_MILD` 줄 (섹션 4 A 전략)

### (3) 변경 코드

**1-A** — 앵커: `PRESERVATION_BUFFER_PCT = 0.002 # 최소 이익 보존선 슬리피지 허용 버퍼 (0.2%)`
```diff
 DAILY_EVAL_END_MIN = 15        # 일봉 마감 평가 종료 분 (09:15 KST)
 PRESERVATION_BUFFER_PCT = 0.002 # 최소 이익 보존선 슬리피지 허용 버퍼 (0.2%)
+STAGE2_SIGNAL_MODE = "closed_candle"  # 2단계 4H 5MA 이탈 판정: "closed_candle"(마감된 4H 완성봉 종가, 권장) | "realtime"(진행 중 캔들 실시간가)
```

**1-B** — 앵커: `UNIT_DIVISOR = 10.0            # 1.0 Unit = 기준 잔고 / 10`
```diff
-UNIT_DIVISOR = 10.0            # 1.0 Unit = 기준 잔고 / 10
+UNIT_DIVISOR = 12.0            # 1.0 Unit = 기준 잔고 / 12 (1.0x×4 + 1.25x×6 = 11.5 Unit 소요 → 0.5 Unit 여유)
+MAX_EXECUTED_UNITS = 10        # 싸이클당 실제 매수 최대 회차 (UNIT_DIVISOR와 분리된 독립 상한)
+MARGIN_SAFETY_RATIO = 0.95     # 1회 주문금액 상한 = 가용 잔고 × 레버리지 × 0.95 (증거금 부족 주문 거절 방지)
 MIN_ORDER_USD = 5.0            # Bitget 선물 최소 주문 가능 금액 ($5.0)
```

**1-C** — 앵커: `LONG_EXIT_FALLING_SMA_MILD = 0.99`
```diff
 LONG_EXIT_RISING_SMA = 1.02            # 60일선 상승 시 Target = SMA5 * 1.02 이상
-LONG_EXIT_FALLING_SMA_MILD = 0.99      # 60일선 하락 & 낙폭 > -30% Target = SMA5 * 0.99 이하 (본전탈출)
+LONG_EXIT_FALLING_SMA_MILD = 0.99      # 60일선 하락 & 낙폭 > -30% Target = SMA5 * 0.99 이상 회복 시 즉시 청산 (본전탈출)
+BREAKEVEN_GUARD_ENABLED = False        # (선택) 본전탈출 시 현재가 >= 평단 × (1 + BREAKEVEN_GUARD_MIN_PCT) 일 때만 청산
+BREAKEVEN_GUARD_MIN_PCT = 0.002        # 본전 가드 최소 수익률 (0.2% ≈ 왕복 수수료)
 LONG_EXIT_FALLING_SMA_DEEP = 1.03      # 60일선 하락 & 낙폭 <= -30% Target = SMA5 * 1.03 이상 (반등익절)
```

### (4) 의존성/주의
- `UNIT_DIVISOR`의 의미(1 Unit 분모)는 그대로입니다. 최대 회차 표시/제한은 반드시 `MAX_EXECUTED_UNITS`를 사용합니다. `UNIT_DIVISOR`를 회차 수로 쓰는 코드는 Step 5에서 제거합니다.
- `MARGIN_SAFETY_RATIO`는 Step 8에서 `LEVERAGE`(기존 상수, 1)와 함께 사용됩니다.
- `STAGE2_SIGNAL_MODE`를 `"realtime"`으로 바꾸면 Phase 1과 같은 판정(진행 중 캔들 포함)으로 돌아갑니다. 단, 보존선 우선 판정은 그대로 유지됩니다.

### Step 검증
```powershell
python -m py_compile config.py
python -c "import config as c; print(c.UNIT_DIVISOR, c.MAX_EXECUTED_UNITS, c.MARGIN_SAFETY_RATIO, c.STAGE2_SIGNAL_MODE, c.BREAKEVEN_GUARD_ENABLED)"
```
기대 출력: `12.0 10 0.95 closed_candle False`

---

## Step 2. `strategy/strategy_a_long.py` (Diff)

### (1) 파일 경로
`strategy/strategy_a_long.py`

### (2) 기존 코드 영역 (Phase 1에서 미변경 → 원본 줄 번호 유효)
- 2-A: L9-16 config import
- 2-B: L61 `check_stage1_exit_signal` docstring
- 2-C: L79-85 완만한 하락장 본전탈출 분기
- 2-D: L94-115 `check_stage2_exit_signal()` 메서드 전체

### (3) 변경 코드

**2-A** import
```diff
 from config import (
     LONG_BEARISH_CANDLE_PCT,
     LONG_DAILY_DROP_PCT,
     DRAWDOWN_THRESHOLD,
     LONG_EXIT_RISING_SMA,
     LONG_EXIT_FALLING_SMA_MILD,
-    LONG_EXIT_FALLING_SMA_DEEP
+    LONG_EXIT_FALLING_SMA_DEEP,
+    BREAKEVEN_GUARD_ENABLED,
+    BREAKEVEN_GUARD_MIN_PCT,
+    STAGE2_SIGNAL_MODE
 )
```

**2-B** docstring — 앵커: `- action_type: 'ENTER_STAGE2' (상승장/과낙폭 익절), 'IMMEDIATE_EXIT' (완만한 하락 본탈), 'NONE'`
```diff
             Tuple[is_met, action_type, reason, target_price]
             - action_type: 'ENTER_STAGE2' (상승장/과낙폭 익절), 'IMMEDIATE_EXIT' (완만한 하락 본탈), 'NONE'
+            - 완만한 하락 본탈: 현재가가 SMA5*0.99 이상으로 '회복'했을 때 즉시 청산 (반등 시 탈출)
         """
```

**2-C** 본전탈출 조건 — 앵커: `is_met = current_price <= target_price` (이 파일에 1회만 존재)
```diff
             if drawdown_rate > DRAWDOWN_THRESHOLD:
                 # 완만한 하락장: 본전 탈출 (2단계 없이 즉시 청산)
                 target_price = sma5 * LONG_EXIT_FALLING_SMA_MILD
-                is_met = current_price <= target_price
+                is_met = current_price >= target_price
                 action = "IMMEDIATE_EXIT" if is_met else "NONE"
-                reason = f"60일선 하락장 & 낙폭률({drawdown_rate*100:.1f}% > -30%): SMA5*0.99 목표가(${target_price:,.2f}) 도달 즉시 본탈"
+                reason = f"60일선 하락장 & 낙폭률({drawdown_rate*100:.1f}% > -30%): 현재가 ≥ SMA5*0.99 목표가(${target_price:,.2f}) 회복 → 즉시 본탈"
+                if is_met and BREAKEVEN_GUARD_ENABLED:
+                    avg_price = float(cycle_info.get('avg_price', 0.0) or 0.0)
+                    guard_price = avg_price * (1.0 + BREAKEVEN_GUARD_MIN_PCT)
+                    if avg_price > 0 and current_price < guard_price:
+                        return False, "NONE", (
+                            f"{reason} | 본전 가드 미달(현재가 ${current_price:,.2f} < 평단×{1.0 + BREAKEVEN_GUARD_MIN_PCT:.3f} ${guard_price:,.2f}) → 보유 유지"
+                        ), target_price
                 return is_met, action, reason, target_price
             else:
```

**2-D** `check_stage2_exit_signal()` 전체 교체 — 앵커: `def check_stage2_exit_signal(` 부터 `return False, f"추세 유지 중 (현재가: ...)"` 줄까지 (원본 L94-115)
```diff
     def check_stage2_exit_signal(
         self,
         current_price: float,
         indicators_4h: Dict[str, Any],
         trailing_base_price: float,
-        buffer_pct: float = 0.002
+        buffer_pct: float = 0.002,
+        trailing_activated_ts: int = 0,
+        signal_mode: str = STAGE2_SIGNAL_MODE
     ) -> Tuple[bool, str]:
         """
         롱 2단계 트레일링 매도 평가 (5분 주기 감시):
-        1) 4시간봉 5MA 하향 이탈: current_price < sma5_4h
-        2) 최소 이익 보존선 하회: current_price <= trailing_base_price * (1.0 - buffer_pct)
+        1) 최소 이익 보존선 하회 (실시간가, 매 5분): current_price <= trailing_base_price * (1.0 - buffer_pct)
+        2) 4시간봉 5MA 하향 이탈:
+           - closed_candle(기본): 트레일링 돌입 이후 마감된 4H 완성봉 종가 < 완성봉 SMA5
+           - realtime: current_price < 진행 중 캔들 포함 SMA5 (기존 방식)
         """
-        sma5_4h = indicators_4h.get('sma5_4h', 0.0)
-        is_below_sma5 = indicators_4h.get('is_below_sma5_4h', current_price < sma5_4h)
         preservation_floor = trailing_base_price * (1.0 - buffer_pct) if trailing_base_price > 0 else 0.0
-
-        if is_below_sma5:
-            return True, f"4시간봉 5MA(${sma5_4h:,.2f}) 하향 이탈 발생 (추세 꺾임 익절)"
-        elif preservation_floor > 0 and current_price <= preservation_floor:
+        if preservation_floor > 0 and current_price <= preservation_floor:
             return True, f"최소 이익 보존선(${preservation_floor:,.2f}) 하회 발생 (익절 방어 청산)"
 
-        return False, f"추세 유지 중 (현재가: ${current_price:,.2f}, 4H SMA5: ${sma5_4h:,.2f}, 보존선: ${preservation_floor:,.2f})"
+        if signal_mode == "realtime":
+            sma5_4h = indicators_4h.get('sma5_4h', 0.0)
+            if indicators_4h.get('is_below_sma5_4h', current_price < sma5_4h):
+                return True, f"4시간봉 5MA(${sma5_4h:,.2f}) 하향 이탈 발생 (실시간 기준, 추세 꺾임 익절)"
+            return False, f"추세 유지 중 [실시간] (현재가: ${current_price:,.2f}, 4H SMA5: ${sma5_4h:,.2f}, 보존선: ${preservation_floor:,.2f})"
+
+        closed_close = float(indicators_4h.get('closed_close', 0.0) or 0.0)
+        closed_sma5 = float(indicators_4h.get('closed_sma5_4h', 0.0) or 0.0)
+        closed_ts = int(indicators_4h.get('closed_candle_close_ts', 0) or 0)
+        closed_dt = indicators_4h.get('closed_candle_close_dt', '')
+        is_fresh = closed_ts > int(trailing_activated_ts or 0)
+        if closed_sma5 > 0 and is_fresh and indicators_4h.get('is_closed_below_sma5_4h', False):
+            return True, f"4시간봉 완성봉({closed_dt} 마감) 종가 ${closed_close:,.2f} < 5MA ${closed_sma5:,.2f} 하향 이탈 (추세 꺾임 익절)"
+
+        wait_txt = "" if is_fresh else " | 트레일링 돌입 이후 마감된 4H봉 대기 중"
+        return False, (
+            f"추세 유지 중 [완성봉] (현재가: ${current_price:,.2f}, 완성봉 종가: ${closed_close:,.2f}, "
+            f"완성봉 SMA5: ${closed_sma5:,.2f}, 보존선: ${preservation_floor:,.2f}{wait_txt})"
+        )
```

**2-D 적용 후 최종 메서드 (교체 결과 확인용)**
```python
    def check_stage2_exit_signal(
        self,
        current_price: float,
        indicators_4h: Dict[str, Any],
        trailing_base_price: float,
        buffer_pct: float = 0.002,
        trailing_activated_ts: int = 0,
        signal_mode: str = STAGE2_SIGNAL_MODE
    ) -> Tuple[bool, str]:
        """
        롱 2단계 트레일링 매도 평가 (5분 주기 감시):
        1) 최소 이익 보존선 하회 (실시간가, 매 5분): current_price <= trailing_base_price * (1.0 - buffer_pct)
        2) 4시간봉 5MA 하향 이탈:
           - closed_candle(기본): 트레일링 돌입 이후 마감된 4H 완성봉 종가 < 완성봉 SMA5
           - realtime: current_price < 진행 중 캔들 포함 SMA5 (기존 방식)
        """
        preservation_floor = trailing_base_price * (1.0 - buffer_pct) if trailing_base_price > 0 else 0.0
        if preservation_floor > 0 and current_price <= preservation_floor:
            return True, f"최소 이익 보존선(${preservation_floor:,.2f}) 하회 발생 (익절 방어 청산)"

        if signal_mode == "realtime":
            sma5_4h = indicators_4h.get('sma5_4h', 0.0)
            if indicators_4h.get('is_below_sma5_4h', current_price < sma5_4h):
                return True, f"4시간봉 5MA(${sma5_4h:,.2f}) 하향 이탈 발생 (실시간 기준, 추세 꺾임 익절)"
            return False, f"추세 유지 중 [실시간] (현재가: ${current_price:,.2f}, 4H SMA5: ${sma5_4h:,.2f}, 보존선: ${preservation_floor:,.2f})"

        closed_close = float(indicators_4h.get('closed_close', 0.0) or 0.0)
        closed_sma5 = float(indicators_4h.get('closed_sma5_4h', 0.0) or 0.0)
        closed_ts = int(indicators_4h.get('closed_candle_close_ts', 0) or 0)
        closed_dt = indicators_4h.get('closed_candle_close_dt', '')
        is_fresh = closed_ts > int(trailing_activated_ts or 0)
        if closed_sma5 > 0 and is_fresh and indicators_4h.get('is_closed_below_sma5_4h', False):
            return True, f"4시간봉 완성봉({closed_dt} 마감) 종가 ${closed_close:,.2f} < 5MA ${closed_sma5:,.2f} 하향 이탈 (추세 꺾임 익절)"

        wait_txt = "" if is_fresh else " | 트레일링 돌입 이후 마감된 4H봉 대기 중"
        return False, (
            f"추세 유지 중 [완성봉] (현재가: ${current_price:,.2f}, 완성봉 종가: ${closed_close:,.2f}, "
            f"완성봉 SMA5: ${closed_sma5:,.2f}, 보존선: ${preservation_floor:,.2f}{wait_txt})"
        )
```

### (4) 의존성/주의
- 2-C는 **롱 전략만** 변경합니다. 숏 본전탈출(`current_price <= SMA5×1.01`)은 이미 대칭 구조이므로 변경하지 않습니다.
- 2-C의 본전 가드는 `BREAKEVEN_GUARD_ENABLED=False`(기본)이면 동작하지 않습니다. 켜면 SMA5×0.99를 회복해도 평단+0.2% 미만이면 보유를 유지합니다.
- 2-D의 새 키(`closed_close`, `closed_sma5_4h`, `closed_candle_close_ts`, `closed_candle_close_dt`, `is_closed_below_sma5_4h`)는 Step 4의 `calculate_4h()`가 만듭니다. 키가 없으면 `.get` 기본값 때문에 **청산 신호가 나지 않는 방향**(안전측)으로 동작합니다.
- 보존선 판정을 SMA 판정보다 **먼저** 수행합니다. 두 조건이 동시에 충족될 때 결과는 똑같이 True이고 사유 문구만 달라집니다.
- `check_exit_signal()` 래퍼(L117~)는 변경하지 않습니다.

### Step 검증
```powershell
python -m py_compile strategy/strategy_a_long.py
python -c "from strategy.strategy_a_long import StrategyALong as S; s=S(); ci={'total_qty':1,'executed_units':1,'avg_price':100}; i={'sma5':100,'is_sma60_rising':False,'drawdown_rate':-0.1}; print(s.check_stage1_exit_signal(98.0,i,ci)[:2], s.check_stage1_exit_signal(99.5,i,ci)[:2])"
```
기대 출력: `(False, 'NONE') (True, 'IMMEDIATE_EXIT')`

---

## Step 3. `strategy/strategy_b_short.py` (Diff)

### (1) 파일 경로
`strategy/strategy_b_short.py`

### (2) 기존 코드 영역 (Phase 1에서 미변경 → 원본 줄 번호 유효)
- 3-A: L9-16 config import
- 3-B: L94-115 `check_stage2_exit_signal()` 메서드 전체

### (3) 변경 코드

**3-A** import
```diff
 from config import (
     SHORT_BULLISH_CANDLE_PCT,
     SHORT_DAILY_PUMP_PCT,
     REBOUND_THRESHOLD,
     SHORT_EXIT_RISING_SMA,
     SHORT_EXIT_FALLING_SMA_MILD,
-    SHORT_EXIT_FALLING_SMA_DEEP
+    SHORT_EXIT_FALLING_SMA_DEEP,
+    STAGE2_SIGNAL_MODE
 )
```

**3-B** `check_stage2_exit_signal()` 전체 교체 — 앵커: `def check_stage2_exit_signal(` 부터 `return False, f"숏 추세 유지 중 (현재가: ...)"` 줄까지 (원본 L94-115)
```diff
     def check_stage2_exit_signal(
         self,
         current_price: float,
         indicators_4h: Dict[str, Any],
         trailing_base_price: float,
-        buffer_pct: float = 0.002
+        buffer_pct: float = 0.002,
+        trailing_activated_ts: int = 0,
+        signal_mode: str = STAGE2_SIGNAL_MODE
     ) -> Tuple[bool, str]:
         """
         숏 2단계 트레일링 매도 평가 (5분 주기 감시):
-        1) 4시간봉 5MA 상향 돌파: current_price > sma5_4h
-        2) 최소 이익 보존선 상회: current_price >= trailing_base_price * (1.0 + buffer_pct)
+        1) 최소 이익 보존선 상회 (실시간가, 매 5분): current_price >= trailing_base_price * (1.0 + buffer_pct)
+        2) 4시간봉 5MA 상향 돌파:
+           - closed_candle(기본): 트레일링 돌입 이후 마감된 4H 완성봉 종가 > 완성봉 SMA5
+           - realtime: current_price > 진행 중 캔들 포함 SMA5 (기존 방식)
         """
-        sma5_4h = indicators_4h.get('sma5_4h', 0.0)
-        is_above_sma5 = indicators_4h.get('is_above_sma5_4h', current_price > sma5_4h)
         preservation_ceiling = trailing_base_price * (1.0 + buffer_pct) if trailing_base_price > 0 else 0.0
-
-        if is_above_sma5:
-            return True, f"4시간봉 5MA(${sma5_4h:,.2f}) 상향 돌파 발생 (하락세 반등 숏 청산)"
-        elif preservation_ceiling > 0 and current_price >= preservation_ceiling:
+        if preservation_ceiling > 0 and current_price >= preservation_ceiling:
             return True, f"최소 이익 보존선(${preservation_ceiling:,.2f}) 상회 발생 (익절 방어 숏 청산)"
 
-        return False, f"숏 추세 유지 중 (현재가: ${current_price:,.2f}, 4H SMA5: ${sma5_4h:,.2f}, 보존선: ${preservation_ceiling:,.2f})"
+        if signal_mode == "realtime":
+            sma5_4h = indicators_4h.get('sma5_4h', 0.0)
+            if indicators_4h.get('is_above_sma5_4h', current_price > sma5_4h):
+                return True, f"4시간봉 5MA(${sma5_4h:,.2f}) 상향 돌파 발생 (실시간 기준, 하락세 반등 숏 청산)"
+            return False, f"숏 추세 유지 중 [실시간] (현재가: ${current_price:,.2f}, 4H SMA5: ${sma5_4h:,.2f}, 보존선: ${preservation_ceiling:,.2f})"
+
+        closed_close = float(indicators_4h.get('closed_close', 0.0) or 0.0)
+        closed_sma5 = float(indicators_4h.get('closed_sma5_4h', 0.0) or 0.0)
+        closed_ts = int(indicators_4h.get('closed_candle_close_ts', 0) or 0)
+        closed_dt = indicators_4h.get('closed_candle_close_dt', '')
+        is_fresh = closed_ts > int(trailing_activated_ts or 0)
+        if closed_sma5 > 0 and is_fresh and indicators_4h.get('is_closed_above_sma5_4h', False):
+            return True, f"4시간봉 완성봉({closed_dt} 마감) 종가 ${closed_close:,.2f} > 5MA ${closed_sma5:,.2f} 상향 돌파 (하락세 반등 숏 청산)"
+
+        wait_txt = "" if is_fresh else " | 트레일링 돌입 이후 마감된 4H봉 대기 중"
+        return False, (
+            f"숏 추세 유지 중 [완성봉] (현재가: ${current_price:,.2f}, 완성봉 종가: ${closed_close:,.2f}, "
+            f"완성봉 SMA5: ${closed_sma5:,.2f}, 보존선: ${preservation_ceiling:,.2f}{wait_txt})"
+        )
```

### (4) 의존성/주의
- 롱(Step 2-D)과 **부호만 반대인 대칭 구조**입니다. 사용하는 키는 `is_closed_above_sma5_4h`(숏)입니다. 롱이 쓰는 `is_closed_below_sma5_4h`와 혼동하지 않습니다.
- 1차 청산(`check_stage1_exit_signal`)은 변경하지 않습니다.

### Step 검증
```powershell
python -m py_compile strategy/strategy_b_short.py
```

---

## Step 4. `strategy/indicator.py` (Diff)

### (1) 파일 경로
`strategy/indicator.py`

### (2) 기존 코드 영역 (Phase 1에서 미변경 → 원본 줄 번호 유효)
- 4-A: L6-8 import
- 4-B: L92-122 `calculate_4h()` 메서드 전체 (파일 끝까지). `calculate()`(일봉, L16-90)는 **변경하지 않습니다**.

### (3) 변경 코드

**4-A** import
```diff
+import time
+from datetime import datetime, timezone, timedelta
 from typing import Dict, Any, Optional
 import pandas as pd
+from config import SMA_4H_PERIOD
 from utils.logger import logger
+
+KST = timezone(timedelta(hours=9))
+FOUR_HOURS_MS = 4 * 60 * 60 * 1000
```

**4-B** `calculate_4h()` 전체 교체 — 앵커: `    @staticmethod\n    def calculate_4h(df_4h: pd.DataFrame) -> Dict[str, Any]:` 부터 파일 끝 `return indicators_4h` 까지를 아래 코드로 교체
```python
    @staticmethod
    def calculate_4h(df_4h: pd.DataFrame, now_ms: Optional[int] = None) -> Dict[str, Any]:
        """
        4시간봉 OHLCV 데이터를 분석하여 2단계 트레일링 매도 판단 지표를 반환합니다.
        - 실시간 지표(realtime 모드): 진행 중 캔들(iloc[-1]) 포함 SMA5 vs 현재가 (기존 방식)
        - 완성봉 지표(closed_candle 모드): 마감 완료된 4H 캔들만으로 SMA5 vs 최근 완성봉 종가
          (완성 판정: 캔들 시작 timestamp + 4시간 <= now_ms)
        """
        min_len = SMA_4H_PERIOD + 1
        if df_4h.empty or len(df_4h) < min_len:
            raise ValueError(f"4시간봉 지표 산출을 위해서는 최소 {min_len}개 이상의 캔들이 필요합니다. (현재: {len(df_4h)}개)")

        now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
        close_series = df_4h['close'].astype(float)
        sma5_4h = float(close_series.rolling(window=SMA_4H_PERIOD).mean().iloc[-1])
        current_price = float(close_series.iloc[-1])
        prev_close = float(close_series.iloc[-2])

        # [실시간] 4H 5MA 대비 위치 (진행 중 캔들 포함)
        is_below_sma5_4h = current_price < sma5_4h  # 롱 꺾임 (하향 이탈)
        is_above_sma5_4h = current_price > sma5_4h  # 숏 꺾임 (상향 돌파)

        # [완성봉] 마감 완료된 캔들만 사용
        if 'timestamp' in df_4h.columns:
            close_ts_series = df_4h['timestamp'].astype('int64') + FOUR_HOURS_MS
        else:
            # timestamp 컬럼이 없으면 마지막 캔들을 진행 중으로 간주
            close_ts_series = pd.Series([0] * (len(df_4h) - 1) + [now_ms + 1], index=df_4h.index)
        closed_mask = close_ts_series <= now_ms
        closed_closes = close_series[closed_mask]

        if len(closed_closes) >= SMA_4H_PERIOD:
            closed_close = float(closed_closes.iloc[-1])
            closed_sma5_4h = float(closed_closes.iloc[-SMA_4H_PERIOD:].mean())
            closed_candle_close_ts = int(close_ts_series[closed_mask].iloc[-1])
            closed_candle_close_dt = (
                datetime.fromtimestamp(closed_candle_close_ts / 1000, tz=KST).strftime('%Y-%m-%d %H:%M')
                if closed_candle_close_ts > 0 else ""
            )
            is_closed_below_sma5_4h = closed_close < closed_sma5_4h
            is_closed_above_sma5_4h = closed_close > closed_sma5_4h
        else:
            closed_close = 0.0
            closed_sma5_4h = 0.0
            closed_candle_close_ts = 0
            closed_candle_close_dt = ""
            is_closed_below_sma5_4h = False
            is_closed_above_sma5_4h = False

        indicators_4h = {
            'current_price': current_price,
            'sma5_4h': sma5_4h,
            'prev_close': prev_close,
            'is_below_sma5_4h': is_below_sma5_4h,
            'is_above_sma5_4h': is_above_sma5_4h,
            'closed_close': closed_close,
            'closed_sma5_4h': closed_sma5_4h,
            'closed_candle_close_ts': closed_candle_close_ts,
            'closed_candle_close_dt': closed_candle_close_dt,
            'is_closed_below_sma5_4h': is_closed_below_sma5_4h,
            'is_closed_above_sma5_4h': is_closed_above_sma5_4h,
            'last_candle_date': str(df_4h['datetime'].iloc[-1]) if 'datetime' in df_4h.columns else ""
        }

        logger.info(
            f"[Indicators 4H] 현재가: ${current_price:,.2f}, 실시간 SMA5: ${sma5_4h:,.2f} | "
            f"완성봉({closed_candle_close_dt} 마감) 종가: ${closed_close:,.2f}, 완성봉 SMA5: ${closed_sma5_4h:,.2f} | "
            f"실시간 하향/상향: {is_below_sma5_4h}/{is_above_sma5_4h}, 완성봉 하향/상향: {is_closed_below_sma5_4h}/{is_closed_above_sma5_4h}"
        )
        return indicators_4h
```

### (4) 의존성/주의
- 최소 캔들 수가 **5 → 6**(`SMA_4H_PERIOD + 1`)으로 늘어납니다. 완성봉 5개와 진행 중 캔들 1개가 필요하기 때문입니다. Step 8(trader `< 6`)과 Step 9(main `MIN_4H_CANDLES = 6`)를 반드시 함께 적용합니다.
- 기존 반환 키(`current_price`, `sma5_4h`, `prev_close`, `is_below_sma5_4h`, `is_above_sma5_4h`, `last_candle_date`)는 그대로 유지합니다 (realtime 모드 호환).
- `timestamp`는 Bitget/ccxt가 주는 **캔들 시작 시각(ms, UTC)** 입니다. `bitget_client.fetch_ohlcv()`와 `utils/mock_data.generate_mock_ohlcv()` 모두 이 컬럼을 만듭니다. 모의 데이터의 마지막 캔들은 "지금" 시작하므로 진행 중으로 판정됩니다.
- `config` → `strategy.indicator` 방향의 import만 추가되므로 순환 import는 생기지 않습니다 (`config`는 프로젝트 모듈을 import하지 않음).
- `now_ms` 인자는 테스트용입니다. 운영 코드에서는 생략합니다 (현재 시각 사용).

### Step 검증
```powershell
python -m py_compile strategy/indicator.py
$env:PAPER_TRADING="true"; python -c "from strategy.indicator import IndicatorCalculator as I; from utils.mock_data import generate_mock_ohlcv as g; d=I.calculate_4h(g(30,'4h')); print(d['closed_candle_close_dt'], round(d['closed_sma5_4h'],4), d['is_closed_below_sma5_4h'])"; Remove-Item Env:PAPER_TRADING
```
기대 결과: 예외 없이 `YYYY-MM-DD HH:MM <숫자> <True|False>` 출력.

---

## Step 5. `utils/formatters.py` (Diff)

### (1) 파일 경로
`utils/formatters.py`

### (2) 기존 코드 영역
- L7 import, L14 `max_units` 기본값

### (3) 변경 코드
```diff
 from typing import Optional
-from config import PROGRESS_FILLED_ICON, PROGRESS_EMPTY_ICON, DUMMY_TARGET, UNIT_DIVISOR
+from config import PROGRESS_FILLED_ICON, PROGRESS_EMPTY_ICON, DUMMY_TARGET, MAX_EXECUTED_UNITS
```
```diff
     max_dummy: int = int(DUMMY_TARGET),
-    max_units: int = int(UNIT_DIVISOR),
+    max_units: int = int(MAX_EXECUTED_UNITS),
     filled_char: Optional[str] = None,
```

### (4) 의존성/주의
- 이 변경이 없으면 `UNIT_DIVISOR=12`가 되는 순간 진행바가 **12칸**으로 바뀝니다. 반드시 Step 1과 함께 적용합니다.
- docstring의 "기본 10차" 설명은 `MAX_EXECUTED_UNITS=10`과 일치하므로 수정하지 않습니다.

### Step 검증
```powershell
python -c "from utils.formatters import format_progress_single_track as f; print(f(2,3))"
```
기대 결과: 실제 칸이 10개(`🟨🟨🟨⬛⬛⬛⬛⬛⬛⬛`)이고 `(더미 2/2, 실제 3/10)`.

---

## Step 6. `manager/cycle_manager.py` (Diff)

### (1) 파일 경로
`manager/cycle_manager.py`

### (2) 기존 코드 영역 (Phase 1 적용 후 기준 근사 줄 번호)
- 6-A: L6-15 import 블록 (Phase 1 Step 6-A 결과)
- 6-B: L48 `_default_strategy_state()`의 `"trailing_triggered_at": None,`
- 6-C: L83 `_load_state()`의 `setdefault("trailing_triggered_at", None)`
- 6-D: L141-166 `determine_buy_action()`
- 6-E: L249 `reset_cycle()`의 트레일링 초기화 블록
- 6-F: L290 `set_trailing_mode()`
- 6-G: L297-306 `clear_trailing_mode()` + 바로 뒤에 신규 메서드 `ratchet_trailing_base()` 추가

### (3) 변경 코드

**6-A** import
```diff
 import json
 import os
 import shutil
+import time
 from datetime import datetime, timezone, timedelta
 from pathlib import Path
 from typing import Dict, Any, Tuple, Optional
 from config import (
     STATE_FILE, PAPER_TRADING,
-    DUMMY_TARGET, WEIGHTED_UNIT_THRESHOLD, WEIGHT_NORMAL, WEIGHT_BOOST
+    DUMMY_TARGET, WEIGHTED_UNIT_THRESHOLD, WEIGHT_NORMAL, WEIGHT_BOOST, MAX_EXECUTED_UNITS
 )
```

**6-B** 기본 상태 필드 — 앵커: `"trailing_triggered_at": None,` (`_default_strategy_state` 안, 들여쓰기 12칸)
```diff
             "trailing_reason": "",
             "trailing_triggered_at": None,
+            "trailing_activated_ts": 0,
             "last_realized_pnl": 0.0,
```

**6-C** 기존 state 호환 — 앵커: `data[strat].setdefault("trailing_triggered_at", None)`
```diff
                         data[strat].setdefault("trailing_triggered_at", None)
+                        data[strat].setdefault("trailing_activated_ts", 0)
                         data[strat].setdefault("last_realized_pnl", 0.0)
```

**6-D** 최대 회차 — 앵커: `- action_type: 'DUMMY' or 'REAL_BUY'` 및 `# 2. 실전 매수 단계 (3회차 신호부터 실제 집행)`
```diff
         Returns:
             Tuple[action_type, weight, label]
-            - action_type: 'DUMMY' or 'REAL_BUY'
+            - action_type: 'DUMMY', 'REAL_BUY' 또는 'MAX_REACHED'(최대 회차 도달, 추가 매수 중단)
             - weight: 0.0(더미), 1.0(기본 유닛), 1.25(비중 확대 유닛)
```
```diff
         if dummy_count < DUMMY_TARGET:
             next_dummy = dummy_count + 1
             return "DUMMY", 0.0, f"더미 {next_dummy}회차"
 
-        # 2. 실전 매수 단계 (3회차 신호부터 실제 집행)
+        # 2. 최대 회차 도달 시 추가 매수 중단 (MAX_EXECUTED_UNITS)
+        if executed_units >= MAX_EXECUTED_UNITS:
+            return "MAX_REACHED", 0.0, f"최대 {MAX_EXECUTED_UNITS}회차 도달"
+
+        # 3. 실전 매수 단계 (3회차 신호부터 실제 집행)
         current_unit_seq = executed_units + 1
```

**6-E** `reset_cycle()` — 앵커: `# 트레일링 모드 초기화` 블록 (뒤에 빈 줄 + `if transfer_to_dummy1:`이 오는 위치)
```diff
         # 트레일링 모드 초기화
         s["trailing_mode"] = False
         s["trailing_base_price"] = 0.0
         s["trailing_target_price"] = 0.0
         s["trailing_reason"] = ""
         s["trailing_triggered_at"] = None
+        s["trailing_activated_ts"] = 0
 
         if transfer_to_dummy1:
```

**6-F** `set_trailing_mode()` — 앵커: `s["trailing_triggered_at"] = date_str`
```diff
         s["trailing_reason"] = reason
         s["trailing_triggered_at"] = date_str
+        s["trailing_activated_ts"] = int(time.time() * 1000)   # 4H 완성봉 판정 기준 시각(ms)
         self._save_state(self.state)
```

**6-G** `clear_trailing_mode()` 수정 + `ratchet_trailing_base()` 추가 — 앵커: `logger.info(f"[CycleManager] [{strategy_name}] 2단계 트레일링 감시 해제")`
```diff
         s["trailing_reason"] = ""
         s["trailing_triggered_at"] = None
+        s["trailing_activated_ts"] = 0
         self._save_state(self.state)
         logger.info(f"[CycleManager] [{strategy_name}] 2단계 트레일링 감시 해제")
+
+    def ratchet_trailing_base(
+        self,
+        strategy_name: str,
+        candidate_price: float,
+        reason: str,
+        date_str: str
+    ) -> Tuple[bool, float, float]:
+        """
+        2단계 트레일링 중 일봉 재평가로 산출된 신규 목표가를 최소 이익 보존선에 '유리한 방향으로만' 반영합니다.
+        - 롱(strategy_A): 후보가 기존보다 높을 때만 상향 (max)
+        - 숏(strategy_B): 후보가 기존보다 낮을 때만 하향 (min)
+        Returns: (갱신 여부, 기존 보존선, 적용 후 보존선)
+        """
+        s = self.state[strategy_name]
+        old_base = float(s.get("trailing_base_price", 0.0) or 0.0)
+        if not s.get("trailing_mode", False) or candidate_price <= 0:
+            return False, old_base, old_base
+
+        if strategy_name == "strategy_A":
+            improved = old_base <= 0 or candidate_price > old_base
+        else:
+            improved = old_base <= 0 or candidate_price < old_base
+        if not improved:
+            return False, old_base, old_base
+
+        s["trailing_base_price"] = float(candidate_price)
+        self._save_state(self.state)
+        logger.info(
+            f"[CycleManager] [{strategy_name}] 최소 이익 보존선 래칫 갱신: "
+            f"${old_base:,.2f} → ${candidate_price:,.2f} ({reason}, {date_str})"
+        )
+        return True, old_base, float(candidate_price)
 
     def is_any_trailing_active(self) -> bool:
```

### (4) 의존성/주의
- `s["trailing_triggered_at"] = None` 줄은 `reset_cycle()`과 `clear_trailing_mode()`에 **2번** 있습니다. 6-E와 6-G는 각각 바로 뒤 문맥(`if transfer_to_dummy1:` / `2단계 트레일링 감시 해제` 로그)으로 위치를 구분합니다. 두 곳 모두 수정해야 합니다.
- `trailing_activated_ts`는 epoch **밀리초**(int)이고, 4H 캔들 `timestamp` 단위와 같습니다. 이미 트레일링 중인 기존 `state.json`은 setdefault로 `0`이 되므로 "돌입 이후 완성봉" 제한 없이 즉시 판정됩니다. 기존 보유 포지션에 대한 하위 호환 동작입니다.
- `ratchet_trailing_base()`는 `trailing_target_price`, `trailing_reason`, `last_action`을 **변경하지 않습니다**. 최초 돌입 정보를 보존하기 위해서입니다.
- `determine_buy_action()`이 새 반환값 `"MAX_REACHED"`를 돌려주므로 Step 8의 trader 처리가 **반드시 함께** 적용되어야 합니다. 적용하지 않으면 weight 0.0으로 `_execute_real_entry`가 호출되어 최소 주문금액 스킵으로 빠집니다(주문은 나가지 않지만 메시지가 부정확해짐).

### Step 검증
```powershell
python -m py_compile manager/cycle_manager.py
```

---

## Step 7. `utils/excel_logger.py` (Diff)

### (1) 파일 경로
`utils/excel_logger.py`

### (2) 기존 코드 영역
약 L174-182 `append_record()` 내부 `event_fills` 딕셔너리 (Phase 1 Step 7 결과)

### (3) 변경 코드 — 앵커: `"SYNC_ADOPT": PatternFill(`
```diff
                 "SYNC_RESET": PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid"),  # 연파랑 (대조 복구)
                 "SYNC_ADOPT": PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid"),  # 연파랑 (대조 복구)
+                "TRAIL_RATCHET": PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid"),  # 연노랑 (보존선 갱신)
             }
```

### (4) 의존성/주의
- `TRAIL_RATCHET`에는 `SELL_CLEAR` 문자열이 없으므로 손익차트 집계에서 제외됩니다 (의도된 동작).

### Step 검증
```powershell
python -m py_compile utils/excel_logger.py
```

---

## Step 8. `manager/trader.py` (Diff)

### (1) 파일 경로
`manager/trader.py`

### (2) 기존 코드 영역 (Phase 1 Step 9 전체 교체 결과 기준 — 줄 번호 정확)
- 8-A: L11 config import
- 8-B: L201-209 `_evaluate_stage1_exit()`의 신호 평가 직후
- 8-C: L237-239 `_evaluate_stage1_exit()` 끝과 `_close_position()` 사이에 신규 메서드 `_handle_trailing_ratchet()` 추가
- 8-D: L399 `_evaluate_entry()`의 `determine_buy_action` 호출 직후
- 8-E: L452 `_execute_real_entry()`의 `target_usd = unit_base_usd * weight` 직후
- 8-F: L506 진행 로그의 `({executed}/10)`
- 8-G: L548 `process_stage2_monitoring()`의 최소 캔들 수
- 8-H: L568-573 `check_stage2_exit_signal(...)` 호출 인자

### (3) 변경 코드

**8-A** import — 앵커: `from config import UNIT_DIVISOR, PRESERVATION_BUFFER_PCT, MIN_ORDER_USD, PARTIAL_FILL_RATIO, ALERT_COOLDOWN_SEC`
```diff
-from config import UNIT_DIVISOR, PRESERVATION_BUFFER_PCT, MIN_ORDER_USD, PARTIAL_FILL_RATIO, ALERT_COOLDOWN_SEC
+from config import (
+    UNIT_DIVISOR, MAX_EXECUTED_UNITS, MARGIN_SAFETY_RATIO, LEVERAGE, STAGE2_SIGNAL_MODE,
+    PRESERVATION_BUFFER_PCT, MIN_ORDER_USD, PARTIAL_FILL_RATIO, ALERT_COOLDOWN_SEC
+)
```

**8-B** 트레일링 중 래칫 분기 — 앵커: `cycle_info=state` (파일 내 1회)
```diff
         exit_met, action, exit_reason, target_price = strategy.check_stage1_exit_signal(
             current_price=current_price,
             indicators=indicators,
             cycle_info=state
         )
 
+        # 2단계 트레일링 진행 중: 재돌입/즉시청산 없이 보존선을 유리한 방향으로만 갱신 (래칫)
+        if state.get("trailing_mode", False):
+            self._handle_trailing_ratchet(
+                strategy_name, exit_met, action, exit_reason, target_price, date_str, execution_logs
+            )
+            return False
+
         if not exit_met:
             logger.info(f"[Trader] [{strategy_name}] {label} 1차 청산 조건 미충족 ({exit_reason})")
```

**8-C** 신규 메서드 — 앵커: `_evaluate_stage1_exit()` 마지막 `        return False` 와 `    def _close_position(` 사이
```diff
             execution_logs.append(log_msg)
             return closed
 
         return False
 
+    def _handle_trailing_ratchet(
+        self,
+        strategy_name: str,
+        exit_met: bool,
+        action: str,
+        exit_reason: str,
+        target_price: float,
+        date_str: str,
+        execution_logs: List[str]
+    ):
+        """
+        트레일링 감시 중 일봉 재평가 처리:
+        - ENTER_STAGE2 신규 목표가가 기존 보존선보다 유리할 때만 보존선 갱신 (롱 상향 / 숏 하향)
+        - IMMEDIATE_EXIT / NONE / 불리한 목표가는 무시 (보존선 유지, 청산은 4H 감시가 담당)
+        """
+        cfg = SIDE_CFG[strategy_name]
+        label = cfg["label"]
+        if not (exit_met and action == "ENTER_STAGE2" and target_price > 0):
+            logger.info(f"[Trader] [{strategy_name}] {label} 트레일링 감시 중 - 일봉 재평가 결과 무시 ({action}: {exit_reason})")
+            return
+
+        improved, old_base, new_base = self.cycles.ratchet_trailing_base(
+            strategy_name, target_price, exit_reason, date_str
+        )
+        if not improved:
+            logger.info(
+                f"[Trader] [{strategy_name}] {label} 보존선 유지 (기존 ${old_base:,.2f}, 신규 후보 ${target_price:,.2f} - 불리한 방향)"
+            )
+            return
+
+        direction_txt = "상향" if cfg["direction"] > 0 else "하향"
+        execution_logs.append(f"🔒 [{label} 보존선 {direction_txt}] ${old_base:,.2f} → ${new_base:,.2f} ({exit_reason})")
+        self.excel.append_record(
+            date_str=date_str,
+            strategy_name=strategy_name,
+            event_type="TRAIL_RATCHET",
+            price=None,
+            qty=None,
+            unit_label="보존선 갱신",
+            dummy_status="-",
+            total_balance=self.last_total_balance,
+            note=f"최소 이익 보존선 {direction_txt}: ${old_base:,.2f} → ${new_base:,.2f} ({exit_reason})",
+            auto_update_chart=False
+        )
+
     def _close_position(
```

**8-D** 최대 회차 처리 — 앵커: `action_type, weight, unit_label = self.cycles.determine_buy_action(strategy_name)`
```diff
         action_type, weight, unit_label = self.cycles.determine_buy_action(strategy_name)
+        if action_type == "MAX_REACHED":
+            reason = f"{entry_reason} (최대 {MAX_EXECUTED_UNITS}회차 도달 - 추가 매수 중단)"
+            logger.info(f"[Trader] [{strategy_name}] {label} {reason}")
+            execution_logs.append(f"⏸️ [{label} 추가 매수 중단] 최대 {MAX_EXECUTED_UNITS}회차 도달 ({entry_reason})")
+            self._set_eval(
+                strategy_name, True, "SKIPPED_MAX_UNITS", reason,
+                f"⏸️ 최대 {MAX_EXECUTED_UNITS}회차 도달로 추가 매수 중단"
+            )
+            return
+
         if action_type == "DUMMY":
```

**8-E** 가용 잔고 상한 — 앵커: `target_usd = unit_base_usd * weight`
```diff
         target_usd = unit_base_usd * weight
 
+        # 가용 잔고 상한: 증거금 부족으로 인한 주문 거절 방지 (가용 잔고 × 레버리지 × 안전비율)
+        margin_cap_usd = self.last_free_margin * LEVERAGE * MARGIN_SAFETY_RATIO
+        if target_usd > margin_cap_usd:
+            logger.warning(
+                f"[Trader] [{strategy_name}] 주문금액 ${target_usd:,.2f} → 가용 잔고 상한 ${margin_cap_usd:,.2f}로 축소 "
+                f"(가용 ${self.last_free_margin:,.2f} × {LEVERAGE}x × {MARGIN_SAFETY_RATIO})"
+            )
+            target_usd = margin_cap_usd
+            basis_label = f"{basis_label}, 가용상한 적용"
+
         # 최소 주문금액 가드
         if target_usd < MIN_ORDER_USD:
```

**8-F** 진행 로그 — 앵커: `| {bar} ({executed}/10)"`
```diff
-            f"(${invested_usd:,.1f}) [{basis_label}] | {bar} ({executed}/10)"
+            f"(${invested_usd:,.1f}) [{basis_label}] | {bar} ({executed}/{MAX_EXECUTED_UNITS})"
```

**8-G** 4H 최소 캔들 — 앵커: `if df_4h.empty or len(df_4h) < 5:`
```diff
-        if df_4h.empty or len(df_4h) < 5:
+        if df_4h.empty or len(df_4h) < 6:
             logger.warning("[Trader 4H] 4시간봉 캔들이 부족하여 2단계 감시를 건너뜁니다.")
```

**8-H** 2단계 판정 인자 — 앵커: `buffer_pct=PRESERVATION_BUFFER_PCT` (파일 내 1회)
```diff
                 exit_met, reason_2 = self.strategies[name].check_stage2_exit_signal(
                     current_price=current_price,
                     indicators_4h=indicators_4h,
                     trailing_base_price=state.get("trailing_base_price", 0.0),
-                    buffer_pct=PRESERVATION_BUFFER_PCT
+                    buffer_pct=PRESERVATION_BUFFER_PCT,
+                    trailing_activated_ts=int(state.get("trailing_activated_ts", 0) or 0),
+                    signal_mode=STAGE2_SIGNAL_MODE
                 )
```

### (4) 의존성/주의
- **8-B의 위치가 중요합니다.** `if not exit_met:`보다 **앞**에 있어야 트레일링 중 `NONE`/`IMMEDIATE_EXIT`가 모두 무시됩니다. 이 분기가 없으면 Phase 1 코드는 트레일링 중 `ENTER_STAGE2`를 다시 받을 때 `set_trailing_mode()`로 보존선을 **낮출 수 있고**, `IMMEDIATE_EXIT`이면 즉시 청산합니다.
- 8-B는 `return False`(당일 청산 아님)를 반환합니다. 진입 평가는 기존 `_evaluate_entry()`의 `TRAILING_ACTIVE` 분기로 자동 스킵됩니다.
- 8-E의 상한은 **최소 주문금액 가드보다 먼저** 적용합니다. 상한으로 줄어든 금액이 $5 미만이면 기존 `SKIPPED_MIN_ORDER`로 스킵됩니다.
- PAPER 모드의 가용 잔고는 `MOCK_BALANCE - Σ(보유수량×평단)`으로 계산됩니다 (Phase 1 `bitget_client`). 따라서 PAPER에서도 상한이 동작합니다.
- 8-G/8-H는 Step 2~4와 짝입니다. 일부만 적용하면 `TypeError: unexpected keyword argument 'trailing_activated_ts'`가 발생합니다.
- `UNIT_DIVISOR` import는 그대로 유지합니다 (L90, L137, L446, L449, L595에서 계속 사용).

### Step 검증
```powershell
python -m py_compile manager/trader.py
python -c "import manager.trader as t; print('trader import ok')"
```

---

## Step 9. `main.py` (Diff)

### (1) 파일 경로
`main.py`

### (2) 기존 코드 영역
L47 `MIN_4H_CANDLES` (Phase 1 Step 10 결과)

### (3) 변경 코드 — 앵커: `MIN_4H_CANDLES = 5`
```diff
 MIN_DAILY_CANDLES = 61   # 일봉 지표 산출 최소 캔들 수
-MIN_4H_CANDLES = 5       # 4시간봉 지표 산출 최소 캔들 수
+MIN_4H_CANDLES = 6       # 4시간봉 지표 산출 최소 캔들 수 (완성봉 SMA5 = 완성봉 5개 + 진행 중 1개)
```

### (4) 의존성/주의
- `MIN_DAILY_CANDLES = 61`은 Phase 3에서 62로 바꿉니다. 여기서는 건드리지 않습니다.
- `CANDLE_LIMIT_4H = 30`이므로 정상 수집 시 6개 조건은 항상 충족됩니다.

### Step 검증
```powershell
python -m py_compile main.py
```

---

## Step 10. 문서 동기화 (Diff)

### 10-A. `docs/prd/bitget_v3_switch_prd.md`

**(2) 기존 코드 영역**: L26-27 (Unit 수식), L30 (최소 주문 가드), L35 (가중치), L58 (A 전략 본전탈출), L72 뒤 (B 전략 끝), L81 (config 설명)

**(3) 변경**
```diff
-   \cfrac{\text{Free Margin (실시간 가용 잔고)}}{10.0}, & \text{if 상대편 포지션 보유 중 (양방향 헤지, opposite\_qty > 0)} \\[10pt]
-   \cfrac{\text{Total Balance (총 평가 잔고)}}{10.0}, & \text{if 상대편 포지션 없음 (단일 포지션, opposite\_qty = 0)}
+   \cfrac{\text{Free Margin (실시간 가용 잔고)}}{12.0}, & \text{if 상대편 포지션 보유 중 (양방향 헤지, opposite\_qty > 0)} \\[10pt]
+   \cfrac{\text{Total Balance (총 평가 잔고)}}{12.0}, & \text{if 상대편 포지션 없음 (단일 포지션, opposite\_qty = 0)}
```
```diff
    * **최소 주문금액 가드:** 주문금액이 최소 한도(`MIN_ORDER_USD = 5.0 USDT`) 미만일 경우 안전 스킵(Skip)합니다.
+   * **가용 잔고 상한:** 1회 주문금액은 `가용 잔고 × 레버리지 × MARGIN_SAFETY_RATIO(0.95)`를 넘지 않도록 자동 축소합니다.
+   * **분모 12 근거:** 실제 매수 1~4회차(1.0x×4) + 5~10회차(1.25x×6) = 11.5 Unit 소요 → 12 분할 시 0.5 Unit 여유.
```
```diff
    * **실제 매수 5회차 이상 (`executed_units` $\ge 4$):** $1.25 \times \text{Unit Size}$ (비중 확대 유닛 적용)
+   * **최대 회차:** 싸이클당 실제 매수는 최대 10회차(`MAX_EXECUTED_UNITS = 10`)까지이며, 도달 후 신호는 `SKIPPED_MAX_UNITS`로 스킵합니다.
```
```diff
-   * **60일선 하락 & 낙폭률 $> -30\%$:** Target = $SMA_5 \times 0.99$ 이하 다다를 때 롱 전량 청산 (빠른 본전 탈출)
+   * **60일선 하락 & 낙폭률 $> -30\%$:** 현재가가 Target = $SMA_5 \times 0.99$ **이상으로 회복**할 때 롱 전량 즉시 청산 (반등 시 빠른 본전 탈출)
```
L72(`*(※ 반등폭 = ...)*`) 바로 뒤, `---` 앞에 추가:
```diff
    * *(※ 반등폭 = $(\text{현재가} - \text{싸이클 내 전저점}) / \text{싸이클 내 전저점}$)*
+
+### **2단계 트레일링 공통 규칙 (A/B 대칭)**
+1. **돌입:** 1차 청산 목표가 중 `ENTER_STAGE2` 조건 충족 시 즉시 청산하지 않고, 목표가를 **최소 이익 보존선**으로 설정해 감시를 시작합니다.
+2. **보존선 래칫:** 감시 중 매일 일봉 재평가에서 새 `ENTER_STAGE2` 목표가가 더 유리할 때만 보존선을 갱신합니다 (롱 상향 / 숏 하향). 감시 중 `IMMEDIATE_EXIT`·미충족 결과는 무시합니다.
+3. **청산 (5분 주기):**
+   * 보존선 이탈(롱: 현재가 ≤ 보존선×0.998 / 숏: 현재가 ≥ 보존선×1.002)은 **실시간가**로 즉시 판정합니다.
+   * 4H 5MA 이탈은 **마감된 4H 완성봉 종가 vs 완성봉 SMA5**로 판정하며, 트레일링 돌입 **이후** 마감된 봉만 인정합니다 (`STAGE2_SIGNAL_MODE = "closed_candle"`).
```
```diff
-├── config.py                   # API 키, 파라미터(10등분, -1.5%, -3%, 60일선 배수 등) 설정
+├── config.py                   # API 키, 파라미터(12등분·최대 10회차, -1.5%, -3%, 60일선 배수 등) 설정
```

### 10-B. `README.md`

**(2) 기존 코드 영역**: L65 (A 전략 본전탈출), L68-69 (A 전략 2단계), L83-84 (B 전략 2단계). **L67·L82의 `*/5` 표기는 Phase 3에서 수정**하므로 여기서는 건드리지 않습니다. L396-398 등 과거 업데이트 이력도 수정하지 않습니다.

**(3) 변경**
```diff
-     * **60일선 하락 & 완만한 하락 ($\text{낙폭률} > -30\%$):** Target = $SMA_5 \times 0.99$ 이하 다다를 때 $\rightarrow$ **즉시 시장가 전량 청산 (빠른 본탈/약손절)**
+     * **60일선 하락 & 완만한 하락 ($\text{낙폭률} > -30\%$):** 현재가가 Target = $SMA_5 \times 0.99$ 이상으로 회복할 때 $\rightarrow$ **즉시 시장가 전량 청산 (반등 시 빠른 본탈)**
```
```diff
-     * **추세 꺾임:** 현재가 $<$ 4시간봉 $5MA$ 하향 이탈 **OR**
-     * **이익 방어:** 현재가 $\le$ 최소 이익 보존선 하회 시 $\rightarrow$ **롱 전량 익절 청산!**
+     * **추세 꺾임:** 트레일링 돌입 이후 마감된 **4시간봉 완성봉 종가** $<$ 완성봉 $5MA$ **OR**
+     * **이익 방어:** 실시간 현재가 $\le$ 최소 이익 보존선 하회 시 $\rightarrow$ **롱 전량 익절 청산!**
+     * **보존선 래칫:** 감시 중 일봉 재평가 목표가가 더 높으면 보존선을 상향 (하향 없음)
```
```diff
-     * **추세 반등:** 현재가 $>$ 4시간봉 $5MA$ 상향 돌파 **OR**
-     * **이익 방어:** 현재가 $\ge$ 최소 이익 보존 상한선 상회 시 $\rightarrow$ **숏 전량 익절 청산!**
+     * **추세 반등:** 트레일링 돌입 이후 마감된 **4시간봉 완성봉 종가** $>$ 완성봉 $5MA$ **OR**
+     * **이익 방어:** 실시간 현재가 $\ge$ 최소 이익 보존 상한선 상회 시 $\rightarrow$ **숏 전량 익절 청산!**
+     * **보존선 래칫:** 감시 중 일봉 재평가 목표가가 더 낮으면 보존선을 하향 (상향 없음)
```

### (4) 의존성/주의
- 문서 변경은 코드 동작과 무관하지만 **Step 1~9와 같은 작업 단위에서** 반영합니다 (문서와 코드 불일치 방지).
- README 하단 누적 이력은 `/git-commit` 시에만 작성합니다 (AGENTS.md Rule 4).

---

## Step 11. 통합 검증

### 11-1. 정적 검사 (필수)
```powershell
python -m py_compile config.py main.py strategy/indicator.py strategy/strategy_a_long.py strategy/strategy_b_short.py utils/formatters.py utils/excel_logger.py manager/cycle_manager.py manager/trader.py
python -c "import main; print('import ok')"
```

### 11-2. 단위 시나리오 (state/엑셀 미변경, 가짜 거래소 사용)
아래 스크립트를 `scratch_phase2_check.py`로 **임시 저장 → 실행 → 삭제**합니다 (프로젝트에 남기지 않음).
```python
import os, tempfile, time
os.environ["PAPER_TRADING"] = "true"; os.environ["TELEGRAM_BOT_TOKEN"] = ""
from pathlib import Path
import pandas as pd
from api.bitget_client import OrderResult
from manager.cycle_manager import CycleManager
from manager.trader import Trader
from strategy.indicator import IndicatorCalculator
from strategy.strategy_a_long import StrategyALong

class C:
    paper_trading = True; symbol = "SOXL/USDT:USDT"
    def __init__(s): s.total, s.free, s.orders = 12000.0, 12000.0, []
    def fetch_account_balance(s): return s.total, s.free
    def fetch_open_positions(s): return {"contracts": 0.0}, {"contracts": 0.0}
    def create_hedge_order(s, side, amount, pos_side, is_close=False, ref_price=0.0, symbol=None):
        s.orders.append(round(amount, 4))
        return OrderResult("x", side, pos_side, is_close, amount, amount, ref_price, 0.1, True, {})
class X:
    def append_record(s, **k): pass
class N:
    def send_message(s, m): return True
def ind(p, cc=0.0, dc=0.0, sma5=100.0, rising=True, dd=0.0):
    return dict(current_price=p, prev_close=p, eval_close=p, candle_change=cc, daily_change=dc, sma5=sma5,
                sma60=90.0, is_sma60_rising=rising, drawdown_rate=dd, rebound_rate=0.0, candle_date="2026-10-11 09:00:00")

tmp = Path(tempfile.mkdtemp()); cm = CycleManager(tmp / "s.json"); c = C()
t = Trader(client=c, cycles=cm, excel=X(), telegram=N())
for _ in range(3): t.process_daily_cycle(ind(100.0, cc=-0.02, dc=-0.04))
assert c.orders[-1] == 10.0                        # 12000/12 = $1000 → 10 Qty
c.free = 500.0; t.process_daily_cycle(ind(100.0, cc=-0.02, dc=-0.04))
assert c.orders[-1] == 4.75                        # 가용상한 500×1×0.95
cm.state["strategy_A"]["executed_units"] = 10; t.process_daily_cycle(ind(100.0, cc=-0.02, dc=-0.04))
assert t.last_eval_a["status"] == "SKIPPED_MAX_UNITS"
cm.state["strategy_A"]["executed_units"] = 2
t.process_daily_cycle(ind(103.0, sma5=100.0))      # 트레일링 돌입 (보존선 102)
t.process_daily_cycle(ind(108.0, sma5=105.0))      # 래칫 상향 → 107.1
t.process_daily_cycle(ind(104.0, sma5=101.0))      # 후보 103.02 → 유지
t.process_daily_cycle(ind(99.5, rising=False, dd=-0.05))  # IMMEDIATE_EXIT → 무시
s = cm.get_strategy_state("strategy_A")
assert s["trailing_mode"] and abs(s["trailing_base_price"] - 107.1) < 1e-9
H4 = 4 * 3600 * 1000; cur = (int(time.time() * 1000) // H4) * H4
cl = [110, 111, 112, 113, 114, 115, 116, 117, 118, 109.0]
df = pd.DataFrame({"timestamp": [cur - (9 - i) * H4 for i in range(10)], "open": cl, "high": cl, "low": cl, "close": cl, "volume": [1] * 10})
i4 = IndicatorCalculator.calculate_4h(df)
assert i4["is_below_sma5_4h"] and not i4["is_closed_below_sma5_4h"]   # 진행 중 캔들만 이탈 → 완성봉 기준 유지
assert StrategyALong().check_stage2_exit_signal(109.0, i4, 100.0)[0] is False
print("PHASE2 SCENARIO OK")
```
```powershell
python scratch_phase2_check.py; Remove-Item scratch_phase2_check.py
```
기대 결과: `PHASE2 SCENARIO OK`

### 11-3. PAPER 드라이런 (state/엑셀 백업 후 복원)
> 실제 `state.json`·`trade_history.xlsx`가 변경될 수 있고 텔레그램이 발송되므로 **백업 후 복원**합니다.
```powershell
Copy-Item state.json state.json.p2test; Copy-Item trade_history.xlsx trade_history.xlsx.p2test
$env:PAPER_TRADING = "true"
python main.py --run-once; "exit=$LASTEXITCODE"
python main.py --stage2; "exit=$LASTEXITCODE"
Remove-Item Env:PAPER_TRADING
Copy-Item state.json.p2test state.json -Force; Copy-Item trade_history.xlsx.p2test trade_history.xlsx -Force
Remove-Item state.json.p2test, trade_history.xlsx.p2test
```
기대 결과: 둘 다 `exit=0`. 보고서 `1.0 Unit 기본 금액`이 `잔고/12`로 표시되고 진행바는 10칸입니다.

### 11-4. 운영 전 확인 (사용자)
- 현재 실전 `state.json`에 `trailing_mode: true`인 전략이 있으면, 적용 후 첫 `--stage2` 로그에 `[완성봉]` 판정 문구가 나오는지 확인합니다.
- `executed_units`가 이미 10 이상인 전략이 있으면 이후 진입 신호는 `SKIPPED_MAX_UNITS`로 스킵됩니다 (의도된 동작).

### 롤백
미커밋 상태이므로 `git diff`로 변경을 확인하고, Phase 2 변경 파일만 `git checkout -- <파일>`로 되돌립니다. **주의:** Phase 1도 미커밋이면 checkout 시 Phase 1 변경까지 사라집니다. Phase 2 적용 전에 Phase 1 결과를 `/git-commit` 하거나 파일을 백업해 두는 것을 권장합니다.
