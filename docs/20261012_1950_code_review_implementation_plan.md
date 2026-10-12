# bitget_v3_switch 자동매매 코드 점검 결과 및 개선 구현 계획서

- 작성 일시: 2026-10-12 19:50 (KST)
- 모드: `/ask` (계획서 전용 — 소스 코드 미수정, `/apply` 명령 시에만 반영)
- 점검 범위: `main.py`, `config.py`, `api/bitget_client.py`, `manager/trader.py`, `manager/cycle_manager.py`, `strategy/*.py`, `utils/*.py`, `docs/prd/bitget_v3_switch_prd.md`

> [!CAUTION]
> 현재 `.env`가 **`PAPER_TRADING=false`(실전 모드)** 로 설정되어 있습니다.
> 아래 **P0 항목은 실제 자금 손실 또는 주문 전량 실패로 이어질 수 있으므로**, 반영 전까지는 `PAPER_TRADING=true`로 돌려두는 것을 강력히 권장합니다.

---

## 0. 점검 요약

| ID | 심각도 | 이슈 | 위치 |
| :-- | :-- | :-- | :-- |
| P0-1 | 🔴 치명 | 일봉 평가가 **하루 2회 실행됨** (09:10, 09:15 모두 윈도우 충족) → 더미/매수 중복 | `main.py` `is_daily_evaluation_window` |
| P0-2 | 🔴 치명 | Bitget 헤지모드 주문 파라미터 오류 (`hedged` 누락) → **실전 주문 실패/오동작** | `api/bitget_client.py` `create_hedge_order` |
| P0-3 | 🔴 치명 | 실전 모드에서도 **모의 OHLCV(가격≈30) / 모의 잔고(10,000)** 폴백 후 그대로 주문 | `main.py`, `bitget_client.fetch_account_balance` |
| P0-4 | 🔴 치명 | 청산 시 **주문 전에 실현손익 기록**, 체결 수량/가격 미반영, A 실패 시 B 평가 중단 | `manager/trader.py` |
| P0-5 | 🟠 높음 | `state.json` 손상 시 기본값으로 **덮어써져 포지션 추적 소실**, 거래소 포지션과 대조 없음 | `cycle_manager._load_state` |
| P0-6 | 🟠 높음 | 중복 프로세스 실행 방지 락 없음 (cron + 수동/loop 동시 실행) | `main.py` |
| P1-1 | 🟠 높음 | 트레일링 **돌입 당일** 오래된 state 사본으로 신규 진입 평가 → 추가 매수 가능 | `trader.process_daily_cycle` |
| P1-2 | 🟡 중간 | 트레일링 중에도 1차 청산 재평가 → **최소보존선이 하향 덮어쓰기** 가능 | `trader.process_daily_cycle` |
| P1-3 | 🟠 확인필요 | 롱 "완만한 하락장 본전탈출" 부등호(`<=`)가 숏과 비대칭 → 사실상 손절 | `strategy_a_long.py` L82 |
| P1-4 | 🟡 중간 | 유닛 합계(4×1.0 + 6×1.25 = 11.5U) > 10U, 최대 차수 제한·가용증거금 캡 없음 | `trader`, `cycle_manager` |
| P1-5 | 🟡 중간 | A 주문 후 B 평가 시 **이전 시점 가용잔고** 사용 | `trader.process_daily_cycle` |
| P1-6 | 🟡 중간 | `iloc[-2]`가 "전일 완성봉"인지 검증 없음 | `strategy/indicator.py` |
| P1-7 | ⚪ 선택 | 2단계 감시가 **미완성 4H 캔들** 기준 → 휩쏘 조기청산 가능 | `indicator.calculate_4h` |
| P1-8 | ⚪ 선택 | 전고점/전저점에 고가/저가 미반영 (09:10 현재가만 사용) | `cycle_manager.update_price_extremes` |
| P2-x | 🔵 개선 | 2단계 오류 알림 누락, 재시도 윈도우, loop 견고성, 엑셀 잠금, 수수료, 중복 코드, 테스트 | 다수 |

---

## 1. P0 — 실전 투입 전 필수 수정

### P0-1. 일봉 평가 하루 2회 중복 실행

**원인**
- `config.py`: `DAILY_EVAL_START_MIN = 10`, `DAILY_EVAL_END_MIN = 15`
- [main.py L60-66](../main.py): `10 <= minute <= 15` → Crontab `*/5`가 **09:10, 09:15 두 번 모두** `run_pipeline()` 실행.
- 두 번째 실행도 같은 완성봉(`iloc[-2]`)으로 진입 신호를 재평가 → 더미 1/2→2/2 연속 소진, 혹은 **실전 매수 2회 집행**. `--loop` 모드(300초 + 실행시간 드리프트)도 동일 위험.

**개선**
1. 전략별 멱등성 마커 `last_evaluated_candle` 를 `state.json`에 추가.
2. `Trader.process_daily_cycle()`에서 전략별로 `state[last_evaluated_candle] == indicators['candle_date']`이면 해당 전략의 청산/진입 평가를 스킵.
3. 각 전략의 청산+진입 처리 완료 직후 마커 저장 (부분 실패 시 실패한 전략만 다음 틱에서 재시도).
4. 두 전략 모두 이미 평가된 경우 `run_pipeline()`은 텔레그램 보고 없이 조용히 종료.

```python
# cycle_manager.py
def is_evaluated(self, strategy_name: str, candle_date: str) -> bool:
    return self.state[strategy_name].get("last_evaluated_candle") == candle_date

def mark_evaluated(self, strategy_name: str, candle_date: str):
    self.state[strategy_name]["last_evaluated_candle"] = candle_date
    self._save_state(self.state)
```

```python
# main.py run_pipeline() — 지표 산출 직후
candle_date = indicators['candle_date']
if cycle_manager.is_evaluated("strategy_A", candle_date) and cycle_manager.is_evaluated("strategy_B", candle_date):
    logger.info(f"[Pipeline] {candle_date} 완성봉은 이미 평가 완료 → 스킵")
    return True
```

---

### P0-2. Bitget 헤지모드 주문 파라미터 오류

**원인 (설치된 ccxt 4.5.71 소스 확인 결과)**
`ccxt.bitget.create_order_request`는 `params['hedged']`가 `True`일 때만 `tradeSide='Open'/'Close'`를 설정합니다.

```python
# ccxt/bitget.py (설치본)
hedged, params = self.handle_param_bool(params, 'hedged', False)
...
if reduceOnly:
    if not hedged:
        request['reduceOnly'] = 'YES'          # ← 현재 코드가 타는 분기 (원웨이 모드용)
    else:
        requestSide = 'sell' if (side == 'buy') else 'buy'   # 헤지모드 side 자동 반전
        request['tradeSide'] = 'Close'
else:
    if hedged:
        request['tradeSide'] = 'Open'
```

현재 [bitget_client.py L224-226](../api/bitget_client.py)은 `{'posSide': ..., 'reduceOnly': True}`만 전달 → `posSide`는 ccxt가 해석하지 않는 값이고, `tradeSide`가 누락되어 **헤지모드 계정에서 진입/청산 주문이 거부(예: 40774 unilateral position 오류)** 되거나 의도와 다르게 처리될 수 있습니다.

**개선**
```python
params = {'hedged': True, 'marginMode': MARGIN_MODE}
if is_close:
    params['reduceOnly'] = True   # ccxt가 tradeSide='Close' + side 반전을 자동 처리
# posSide 제거. side는 자연어 의미 그대로 유지 (롱 청산 = 'sell', 숏 청산 = 'buy')
```

추가 보강:
- `setup_exchange()`에서 `self.exchange.load_markets()`를 명시 호출 (`amount_to_precision` 선행조건 보장).
- 포지션 모드 설정 실패를 `debug`가 아닌 `warning`으로 기록하고, 실전 모드에서 계정 `posMode`(Bitget v2 `/api/v2/mix/account/account` 응답의 `posMode == 'hedge_mode'`)를 확인하여 **불일치 시 파이프라인 중단 + 텔레그램 경보**.
- 주문 전 최소 수량 검증: `market['limits']['amount']['min']`, 정밀도 보정 후 `precision_amount <= 0`이면 스킵.

---

### P0-3. 실전 모드의 모의 데이터/모의 잔고 폴백 차단

**원인**
- [main.py L88-95, L242-249](../main.py): OHLCV 수집 실패/부족 시 `generate_mock_ohlcv()`(기준가 30 USD 사인파)로 **그대로 매매 진행**. SOXL 실제가(≈140~160)와 차이가 커서 실전에서 **약 5배 수량 주문** 또는 허위 청산 신호 발생 가능.
- [bitget_client.py L137-144](../api/bitget_client.py): 잔고 조회 실패/0 이하일 때 `MOCK_BALANCE(10,000)` 반환, 가용잔고 0이면 총잔고로 대체 → 유닛 과대 산정.

**개선**
- `PAPER_TRADING=False`일 때:
  - OHLCV 실패/부족 → `RuntimeError` 발생, 주문 없이 종료 + 텔레그램 경보 (모의 데이터는 PAPER 모드 전용).
  - 잔고 조회 실패 → 예외 전파, `free_margin == 0`은 0 그대로 반환.
- 신규 커스텀 예외 `DataUnavailableError` 도입하여 파이프라인에서 명확히 구분 처리.

```python
# main.py
except Exception as e:
    if not PAPER_TRADING:
        raise RuntimeError(f"[LIVE] 일봉 수집 실패로 매매 중단: {e}") from e
    df = generate_mock_ohlcv(CANDLE_LIMIT, timeframe=TIMEFRAME)
```

---

### P0-4. 주문-상태 정합성 (체결 기준 기록 / 실패 격리)

**원인**
1. 청산 경로(즉시청산 L110-117, 2단계 L532-539, L587-594)에서 `record_cycle_realized_pnl()`을 **주문 전에 호출** → 주문 실패 시 누적 실현손익만 늘어나고 포지션은 그대로, 다음날 재청산 시 **이중 집계**.
2. 진입 시 `target_qty`(정밀도 보정 전 값)와 `current_price`로 평단/수량 기록 → 실제 체결 수량·평균가와 괴리 누적 → 청산 시 `reduceOnly` 수량 초과 오류 또는 잔량(dust) 발생.
3. A 전략 주문 예외가 `run_pipeline` 전체 `except`로 전파 → **B 전략 평가 자체가 스킵**.

**개선**
- 순서 고정: `주문 → 체결 확인(fetch_order) → 상태/엑셀 기록`.
- `create_hedge_order()` 반환값에 실체결 정보 정규화:
  ```python
  order = self.exchange.create_order(...)
  filled = self.exchange.fetch_order(order['id'], target_symbol)   # 시장가 주문 응답은 id만 오는 경우가 많음
  return {'id': ..., 'filled': filled['filled'], 'average': filled['average'],
          'fee': (filled.get('fee') or {}).get('cost', 0.0), 'raw': filled}
  ```
- `record_executed_unit(price=order['average'], qty=order['filled'])` 로 기록.
- 청산 수량은 state 값이 아닌 **거래소 실제 포지션 수량**(`fetch_open_positions()` — 현재 미사용 함수) 기준, 실현손익은 실제 체결 평균가 기준으로 계산.
- A/B 전략 처리를 각각 `try/except`로 격리하고, 실패 내역을 `execution_logs`와 텔레그램에 포함.

---

### P0-5. state.json 손상 대응 및 거래소 포지션 대조

**원인**
[cycle_manager.py L82-90](../manager/cycle_manager.py): JSON 로드 실패 시 기본 상태를 반환하고, 이후 첫 `_save_state()`가 **손상 파일을 기본값으로 덮어씀** → 보유 포지션의 평단/차수 정보 영구 소실.

**개선**
1. 로드 실패 시 원본을 `state.corrupt_YYYYMMDD_HHMMSS.json`으로 백업.
2. 실전 모드에서는 기본값으로 진행하지 않고 예외 발생 → 파이프라인 중단 + 텔레그램 경보.
3. 저장 시 직전 정상본을 `state.bak.json`으로 1부 보관.
4. **시작 시 포지션 대조(Reconciliation)**: 실전 모드에서 `fetch_open_positions()` 결과와 state의 `total_qty` 비교 → 허용오차(예: 1% 또는 최소수량) 초과 시 해당 전략 매매 스킵 + 경보.

---

### P0-6. 중복 실행 방지 락

**개선**: `utils/process_lock.py` 신설 — `os.open(lock_path, O_CREAT | O_EXCL)` 기반 PID 락 파일(`run.lock`). 기존 락의 PID가 살아있지 않거나 생성 후 30분 초과 시 stale 락으로 간주하여 제거. `main()` 진입 시 획득, `finally`에서 해제. `.gitignore`에 `*.lock` 추가.

---

## 2. P1 — 전략 로직 정합성

### P1-1. 트레일링 돌입 당일 신규 진입 허용 버그
[trader.py L75-76](../manager/trader.py)에서 가져온 `state_a/state_b` **사본**은 이후 `set_trailing_mode()`가 호출돼도 갱신되지 않음 → L199 / L348의 `trailing_mode` 검사가 당일엔 항상 `False`.
- **개선**: 진입 평가 직전에 `self.cycles.get_strategy_state(...)`로 재조회.

### P1-2. 트레일링 중 1차 청산 재평가
트레일링 활성 상태에서도 매일 `check_stage1_exit_signal()`이 다시 돌아 `set_trailing_mode()`가 `trailing_base_price`를 **새 목표가로 덮어씀** (SMA5가 내려가면 보존선도 하향).
- **개선(권장)**: 트레일링 중이면 1차 평가 스킵. 단, 보존선은 **유리한 방향으로만 래칫**(롱: `max`, 숏: `min`) 갱신 허용.

### P1-3. 롱 "완만한 하락장 본전탈출" 부등호 (⚠️ 사용자 확인 필요)
| 구분 | 상승장 | 완만한 하락/반등 | 깊은 하락/반등 |
| :-- | :-- | :-- | :-- |
| 롱(A) | `>= SMA5×1.02` | **`<= SMA5×0.99`** | `>= SMA5×1.03` |
| 숏(B) | `<= SMA5×0.98` | `<= SMA5×1.01` | `<= SMA5×0.97` |

숏 쪽은 3개 모두 "가격이 유리한 방향(하락)으로 목표 도달"인데, 롱 완만한 하락 케이스만 "가격이 SMA5 아래로 내려가면 청산"입니다. 롱 DCA는 하락일에 매수하므로 **다음날 가격이 SMA5×0.99 이하면 곧바로 손실 청산** → PRD의 "무손절" 원칙과 충돌합니다. PRD 원문(L58)도 "이하"로 되어 있어 **PRD 오기 가능성**이 높습니다.
- **제안**: `current_price >= sma5 * 0.99` (SMA5 근처 회복 시 빠른 탈출)로 수정 + PRD 동시 정정.

### P1-4. 유닛 합계 초과 / 최대 차수 미제한
- 가중치 합: 1~4회차 1.0U ×4 + 5~10회차 1.25U ×6 = **11.5U** (1배 레버리지 10U 한도 초과). 9회차부터 증거금 부족으로 주문 실패 → (P0-4 미수정 시) 파이프라인 전체 중단.
- **개선**:
  - `config.py`에 `MAX_EXECUTED_UNITS = 10`(또는 사용자 결정값) 추가, 도달 시 매수 스킵(`SKIPPED_MAX_UNITS`).
  - `target_usd = min(target_usd, free_margin * 0.95)` 캡, 캡 결과가 `MIN_ORDER_USD` 미만이면 스킵.

### P1-5. 가용잔고 재조회
A 진입 체결 후 B 진입 평가 시 L414의 `free_margin`은 파이프라인 시작 시점 값. → 실전 주문 체결 후 `fetch_account_balance()` 재조회.

### P1-6. 기준 완성봉 검증
`indicator.calculate()`는 `iloc[-1]`=당일 미완성봉, `iloc[-2]`=전일 완성봉이라고 가정. 거래소 응답 지연·`--run-once` 수동 실행 시각에 따라 어긋날 수 있음.
- **개선**: `df['datetime'].iloc[-1]`의 날짜가 KST 오늘(09:00 시작봉)인지 확인, 아니면 실전에서는 중단/경보.

### P1-7. (선택) 2단계 4H 판정 기준
현재가=미완성 4H 캔들 종가, SMA5에도 미완성 캔들이 포함 → 5분마다 노이즈로 이탈 판정 가능.
- 옵션 A: 완성된 4H 캔들(`iloc[-2]`) 종가가 완성봉 SMA5를 이탈할 때만 청산.
- 옵션 B: 현행 유지 + 연속 2틱(10분) 이탈 확인.
- 옵션 C: 현행 유지.

### P1-8. (선택) 전고점/전저점 산출
`update_price_extremes()`가 09:10 현재가만 반영 → 일중 고가/저가 누락. 완성봉 `high`/`low`를 반영하도록 개선 가능 (낙폭률/반등폭 임계 판정에 영향).

---

## 3. P2 — 운영 안정성 및 코드 품질

| ID | 내용 | 개선안 |
| :-- | :-- | :-- |
| P2-1 | `run_stage2_pipeline()` 예외 시 텔레그램 미발송 | 에러 알림 추가 (동일 오류 반복 시 1시간 쿨다운) |
| P2-2 | 09:10 틱 1회 실패 시 09:15 1회만 재시도 | P0-1 멱등성 도입 후 윈도우를 `09:05~09:55`로 확대 → 네트워크 장애에도 재시도 |
| P2-3 | 09:10 틱에는 2단계 감시 스킵 | 일봉 파이프라인 후 트레일링 활성 시 `run_stage2_pipeline()`도 연속 실행 |
| P2-4 | `--loop` 견고성 | 틱마다 `cycle_manager.reload()`, 틱 전체 try/except, 5분 경계 정렬 sleep |
| P2-5 | 엑셀 파일이 열려 있으면 `PermissionError`로 기록 유실 | 3회 재시도 후 `logs/trade_history_fallback.csv`에 백업 기록 |
| P2-6 | 실현손익에 수수료 미반영 | P0-4의 `fetch_order` fee를 PnL·엑셀에 반영 |
| P2-7 | `trader.py` A/B 거의 동일 코드 중복(≈600줄) | `SideConfig`(전략명, 진입/청산 side, 라벨, 이모지) 기반 `_process_exit()`, `_process_entry()`, `_close_position()` 공통화 |
| P2-8 | 미사용 import/변수 (`main.py`의 `PAPER_TRADING`, `BITGET_API_KEY`, `trader.py`의 `order`), `check_exit_signal` 레거시 래퍼 | 정리 |
| P2-9 | PAPER 모드 잔고가 실현손익을 반영하지 않음 | `MOCK_BALANCE + A/B cumulative_realized_pnl`로 시뮬레이션 |
| P2-10 | 자동화 테스트 부재 | `tests/` + pytest 도입 (아래 검증 계획 참고), `requirements-dev.txt` 추가 |

---

## 4. 단계별 구현 계획

### Phase 1 — P0 (실전 안전장치)
| 파일 | 변경 |
| :-- | :-- |
| `api/bitget_client.py` | `hedged` 파라미터, `load_markets`, posMode 검증, 최소수량 검증, `fetch_order` 기반 체결 정규화, 실전 잔고 폴백 제거 |
| `manager/cycle_manager.py` | `last_evaluated_candle` + `is_evaluated/mark_evaluated`, 손상 백업·실전 중단, `state.bak.json`, `reload()` |
| `manager/trader.py` | 주문→체결→기록 순서, 실체결가/수량 기록, 전략별 try/except 격리, 거래소 포지션 기준 청산수량 |
| `main.py` | 실전 모의데이터 폴백 차단, 멱등성 스킵, 프로세스 락, 포지션 대조 호출 |
| `utils/process_lock.py` (신규) | PID 락 |
| `.gitignore` | `*.lock`, `state.bak.json`, `state.corrupt_*.json` |

### Phase 2 — P1 (전략 정합성) — 사용자 결정 반영 후 진행
| 파일 | 변경 |
| :-- | :-- |
| `manager/trader.py` | 진입 전 state 재조회, 트레일링 중 1차 평가 스킵/래칫, 최대차수·가용증거금 캡, 체결 후 잔고 재조회 |
| `strategy/strategy_a_long.py` | (승인 시) MILD 청산 부등호 `>=` |
| `strategy/indicator.py` | 기준봉 날짜 검증, (선택) 4H 완성봉 기준 |
| `config.py` | `MAX_EXECUTED_UNITS`, `MARGIN_SAFETY_RATIO=0.95` |
| `docs/prd/bitget_v3_switch_prd.md` | 확정된 규칙 반영 |

### Phase 3 — P2 (운영/품질)
`main.py`(윈도우·알림·loop), `utils/excel_logger.py`(재시도·CSV 폴백), `trader.py` 리팩토링, `tests/` 신설.

---

## 5. 검증 계획

1. **정적 검사**: `python -m py_compile main.py config.py api/*.py manager/*.py strategy/*.py utils/*.py`
2. **단위 테스트 (pytest, Fake 클라이언트 주입)**
   - 같은 `candle_date`로 `process_daily_cycle()` 2회 호출 → 더미/매수가 1회만 발생.
   - 청산 주문 예외 시 `cumulative_realized_pnl` 불변, A 예외에도 B 평가 진행.
   - 트레일링 돌입 당일 진입 신호 → 매수 스킵.
   - `create_hedge_order(is_close=True)`가 `params`에 `hedged=True, reduceOnly=True` 포함.
   - state.json 손상 → 백업 파일 생성, 실전 모드 예외.
   - `MAX_EXECUTED_UNITS` 도달·가용증거금 캡 동작.
3. **PAPER 모드 드라이런**: `PAPER_TRADING=true`로 `python main.py --run-once` 2회 연속 실행 → 두 번째는 "이미 평가 완료" 로그 확인.
4. **실전 소액 주문 테스트 (사용자 직접 수행 권장)**: 최소 금액(약 $6)으로 롱 진입→청산, 숏 진입→청산 각 1회 → Bitget 앱에서 헤지 포지션 정상 개설/종료 확인.

---

## 6. 사용자 결정 필요 사항

1. **P1-3** 롱 완만한 하락장 청산 조건: `>= SMA5×0.99`로 수정할지, PRD 원문(`<=`) 유지할지?
2. **P1-4** 최대 실제 매수 차수(기본 제안 10회)와 1.25배 가중 구간 유지 여부?
3. **P1-2** 트레일링 중 보존선 처리: 1차 평가 스킵 + 유리한 방향 래칫(권장) / 완전 고정 / 현행?
4. **P1-7** 2단계 4H 판정: 옵션 A(완성봉) / B(연속 2틱 확인) / C(현행)?
5. 적용 범위: Phase 1만 우선 / Phase 1+2 / 전체?
