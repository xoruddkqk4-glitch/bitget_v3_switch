# bitget_v3_switch 자동매매 개선 구현 계획서 (v2 — 사용자 피드백 반영)

- 작성 일시: 2026-10-12 20:13 (KST)
- 모드: `/ask` (계획서 전용 — 소스 코드 미수정)
- 선행 문서: [20261012_1950_code_review_implementation_plan.md](./20261012_1950_code_review_implementation_plan.md) (v1 점검 결과)
- 적용 방식: **Phase 1 → Phase 2 → Phase 3 순차 적용** (예: `/apply Phase 1`). 각 Phase는 독립적으로 적용·검증 가능하도록 구성했습니다.

> [!CAUTION]
> 현재 `.env`는 `PAPER_TRADING=false`(실전)입니다. **Phase 1 적용 및 소액 실주문 검증 완료 전까지 `PAPER_TRADING=true` 유지를 권장**합니다. (현재 헤지모드 주문 파라미터 누락으로 실주문이 거부될 가능성이 높습니다.)

---

## 0. 사용자 피드백 반영 요약

| # | 피드백 | 반영 결과 | Phase |
| :-- | :-- | :-- | :-- |
| 1 | Crontab `3-59/5` 사용 → 일봉 평가 하루 1회 | ✅ 확인. 09:13 틱 1회만 평가 윈도우(09:10~09:15) 충족 → **중복 실행 수정 항목 삭제**. 문서(README `*/5` 표기) 정정만 Phase 3에 포함 | 3 |
| 2 | 헤지모드 주문 설정 추가 | `hedged=True` + `reduceOnly` 적용, 포지션 모드 검증 | **1-1** |
| 3 | 실전 모드 가짜 데이터 매매 절대 금지 | 모의 OHLCV/모의 잔고/모의 주문 폴백 **실전에서 전면 차단**, 시작 시 설정 검증 | **1-2** |
| 4 | 주문과 기록 순서 정합 | `주문 → 체결 확인 → 상태/손익/엑셀 기록`, 실체결가·수량·수수료 반영, 전략별 실패 격리 | **1-3** |
| 5 | 거래소 실제 포지션 대조 절차 | 매 실행 시 Reconciliation + 불일치 전략 매매 차단 + 수동 동기화 CLI | **1-4** |
| 6 | 롱 "완만한 하락장 본전탈출" 조건 변경 | `<= SMA5×0.99` → `>= SMA5×0.99` | **2-1** |
| 7 | 동적 자금 `/10` → `/12` 검토 | ✅ **권장** (시뮬레이션 근거 첨부). 최대 차수 10회 분리 + 가용증거금 캡 동반 | **2-2** |
| 8 | 트레일링 유리한 방향으로만 상향 | 보존선 래칫(롱 `max`/숏 `min`) + 당일 stale state 버그 수정 | **2-3** |
| 9 | 4H 2단계 판정 기준 비교 | **하이브리드(완성봉 추세 이탈 + 실시간 보존선) 권장**, 설정 스위치 제공 | **2-4** |
| 10 | 순차 적용 | Phase 1 → 2 → 3 | — |

---

# Phase 1 — 실전 안전장치 (최우선)

## 1-1. Bitget 헤지모드 주문 설정

### 현재 문제
[bitget_client.py L224-226](../api/bitget_client.py)
```python
params = {'posSide': pos_side}
if is_close:
    params['reduceOnly'] = True
```
설치된 **ccxt 4.5.71** `bitget.create_order_request` 소스 확인 결과:
- `params['hedged']`가 `True`일 때만 `tradeSide='Open'/'Close'`가 설정됨.
- `hedged` 없이 `reduceOnly=True`면 원웨이 모드용 `reduceOnly='YES'`만 전송.
- `posSide`는 ccxt가 해석하지 않는 값(그대로 API로 전달되어 무시/오류 가능).
- 헤지모드 청산 시 ccxt가 side를 **자동 반전**(롱 청산 `sell` → Bitget `buy + tradeSide=Close`)하므로, 호출부는 자연어 의미 그대로 `sell`을 넘기면 됨.

### 변경 내용 (`api/bitget_client.py`)
1. **주문 파라미터**
   ```python
   params = {
       'hedged': True,            # Bitget 헤지모드: tradeSide Open/Close 자동 설정
       'marginMode': MARGIN_MODE, # 'cross'
   }
   if is_close:
       params['reduceOnly'] = True  # ccxt가 tradeSide='Close' + side 반전 처리
   ```
   - `posSide` 제거. 호출부(`trader.py`)의 side 값(롱 진입 `buy`, 롱 청산 `sell`, 숏 진입 `sell`, 숏 청산 `buy`)은 **변경 없음**.
2. **`setup_exchange()` 보강**
   - 맨 앞에서 `self.exchange.load_markets()` 명시 호출 (`amount_to_precision`, `market()` 선행 조건).
   - 포지션/마진/레버리지 설정 실패 로그를 `debug` → `warning`으로 상향.
   - 설정 후 **포지션 모드 검증** (ccxt는 bitget `fetchPositionMode` 미지원 → 암시적 API 사용, 설치본에 존재 확인):
     ```python
     def _verify_hedge_mode(self):
         market = self.exchange.market(self.symbol)
         resp = self.exchange.privateMixGetV2MixAccountAccount({
             'symbol': market['id'],
             'productType': 'USDT-FUTURES',
             'marginCoin': 'USDT',
         })
         data = resp.get('data') or {}
         pos_mode = data.get('posMode')
         if pos_mode != 'hedge_mode':
             raise ExchangeConfigError(f"포지션 모드가 hedge_mode가 아닙니다 (현재: {pos_mode})")
         lev = data.get('crossedMarginLeverage')
         if lev is not None and float(lev) != float(LEVERAGE):
             raise ExchangeConfigError(f"레버리지 불일치 (설정 {LEVERAGE}x / 계정 {lev}x)")
     ```
   - 검증 실패 시 예외 → 파이프라인 중단 + 텔레그램 경보 (주문 0건).
3. **주문 전 수량 검증**
   ```python
   market = self.exchange.market(target_symbol)
   precision_amount = float(self.exchange.amount_to_precision(target_symbol, amount))
   min_amount = (market.get('limits', {}).get('amount', {}) or {}).get('min') or 0.0
   if precision_amount <= 0 or precision_amount < min_amount:
       raise OrderSkipError(f"주문 수량 {precision_amount} < 최소 수량 {min_amount}")
   ```
4. **신규 예외 모듈** `api/exceptions.py`: `ExchangeConfigError`, `DataUnavailableError`, `OrderSkipError`, `OrderNotConfirmedError`, `PositionMismatchError`.

---

## 1-2. 실전 모드 가짜 데이터 매매 전면 차단

### 현재 가짜 데이터 유입 경로 (전수 조사)
| 경로 | 위치 | 실전 위험 |
| :-- | :-- | :-- |
| 일봉 수집 실패/부족 → `generate_mock_ohlcv()` | [main.py L88-95](../main.py) | 기준가 30 USD 사인파로 지표·주문 (실제 SOXL ≈ 140~160 → 약 5배 수량) |
| 4H 수집 실패/부족 → 모의 4H | [main.py L242-249](../main.py) | 허위 이탈 신호로 실포지션 청산 |
| 잔고 조회 실패 → `MOCK_BALANCE` | [bitget_client.py L142-144](../api/bitget_client.py) | 유닛 과대 산정 |
| 잔고 0 이하 → `MOCK_BALANCE`, 가용 0 → 총잔고 | [bitget_client.py L137-138](../api/bitget_client.py) | 헤지 유닛 과대 산정 |
| `PAPER_TRADING=false`인데 API 키 없음 → 주문 **시뮬레이션** | [bitget_client.py L111, L161, L211](../api/bitget_client.py) | 실전이라 믿지만 실제 주문 없음 → 상태만 진행 |
| 포지션 조회 실패 → 0 포지션 반환 | [bitget_client.py L189-191](../api/bitget_client.py) | (1-4 대조 시) 오판 |

### 변경 내용
1. **시작 시 실전 설정 검증** `config.validate_live_config()` (신규, `main()` 최초 호출)
   - `PAPER_TRADING=False`이고 `BITGET_API_KEY/SECRET/PASSPHRASE` 중 하나라도 비어 있으면 즉시 종료(exit code 2) + 텔레그램 경보.
   - `bitget_client.py`의 모든 `or not BITGET_API_KEY` 분기 제거 → 모의 동작 조건은 **오직 `self.paper_trading`**.
2. **모의 OHLCV 격리**
   - `generate_mock_ohlcv()`를 `utils/mock_data.py`로 이동.
   - 함수 첫 줄 가드: `if not PAPER_TRADING: raise RuntimeError("실전 모드에서 모의 데이터 생성 금지")` (이중 안전장치).
   - `main.py` 수집 로직:
     ```python
     def load_ohlcv_or_abort(timeframe, limit, min_len):
         try:
             df = bitget_client.fetch_ohlcv(symbol=SYMBOL, timeframe=timeframe, limit=limit)
         except Exception as e:
             if PAPER_TRADING:
                 logger.warning(f"[PAPER] {timeframe} 수집 실패 → 모의 데이터 사용: {e}")
                 return generate_mock_ohlcv(limit, timeframe)
             raise DataUnavailableError(f"[LIVE] {timeframe} 캔들 수집 실패: {e}") from e
         if df.empty or len(df) < min_len:
             if PAPER_TRADING:
                 return generate_mock_ohlcv(limit, timeframe)
             raise DataUnavailableError(f"[LIVE] {timeframe} 캔들 부족 ({len(df)} < {min_len})")
         return df
     ```
3. **잔고/포지션 조회** (실전)
   - 실패 시 예외 전파 (MOCK 값 반환 금지). 응답이 0이면 0 그대로 반환.
   - `total_equity <= 0` 이면 `DataUnavailableError` (계정 오류로 간주, 주문 금지).
4. **텔레그램 경보 메시지 표준화**: `🚨 [LIVE 매매 중단] 사유 / 단계 / 조치 안내` 형식.

---

## 1-3. 주문 → 체결 확인 → 기록 순서 정합

### 현재 문제
- 청산 3곳(즉시청산 [L110-117](../manager/trader.py), 2단계 롱 [L532-539](../manager/trader.py), 2단계 숏 [L587-594](../manager/trader.py))에서 **`record_cycle_realized_pnl()`이 주문보다 먼저** 실행 → 주문 실패 시 실현손익만 누적, 다음 청산 시 이중 집계.
- 진입 시 정밀도 보정 전 `target_qty`·`current_price`로 평단/수량 기록 → 실제 체결과 괴리.
- A 전략 예외가 파이프라인 전체를 중단시켜 **B 전략 평가 누락**.

### 변경 내용
1. **`OrderResult` 데이터클래스** (`api/bitget_client.py`)
   ```python
   @dataclass
   class OrderResult:
       order_id: str
       filled: float        # 실제 체결 수량
       average: float       # 실제 체결 평균가
       fee: float           # 수수료(USDT, 양수)
       is_paper: bool
       raw: dict
   ```
2. **`create_hedge_order()` 체결 확인 절차** (실전)
   1. 주문 직전 해당 방향 포지션 수량 `pre_qty` 조회.
   2. `create_order()` 실행.
   3. `fetch_order(id)` 최대 3회 폴링(0.5s → 1s → 2s) — `status == 'closed'` 및 `filled > 0` 확인.
   4. `fetch_order` 실패 시 **폴백**: 포지션 재조회 `post_qty`로 `filled = |post_qty - pre_qty|`, 평균가는 포지션 `entryPrice` 변화로 역산(진입) / 현재가(청산).
   5. 모두 실패 → `OrderNotConfirmedError` (상태 기록 안 함, 다음 실행의 1-4 대조에서 감지).
   - PAPER: `filled = 정밀도 보정 수량(소수 4자리)`, `average = current_price`, `fee = notional × PAPER_TAKER_FEE(0.0006)`.
3. **진입 순서** (`trader.py`)
   ```
   주문(OrderResult) → record_executed_unit(price=r.average, qty=r.filled)
                     → excel.append_record(실체결가/수량/수수료)
                     → 상대 전략 미완성 더미 리셋 → 로그/보고
   ```
4. **청산 순서** (`trader.py`) — 공통 메서드 `_close_position(strategy, reason, unit_label)`로 3곳 통합
   ```
   청산 수량 결정 (실전: 거래소 포지션 수량 / PAPER: state 수량)
   → 주문(reduceOnly) → 체결 확인
   → 실현손익 = (체결가 − 평단) × 체결수량 × 방향 − 수수료
   → record_cycle_realized_pnl → excel 기록 → reset_cycle → 로그/보고
   ```
   - **부분 체결**(체결 < 요청의 99%): `total_qty -= filled`, 부분 손익만 기록, `reset_cycle` 보류(트레일링 유지), 경보 발송.
5. **전략별 실패 격리**
   - `process_daily_cycle()` 내부를 `_run_strategy_safely("strategy_A", fn)` 형태로 감싸 A/B 각각 `try/except`.
   - 실패 시 `execution_logs`에 `❌ [롱 처리 실패] 사유` 추가, 일일 보고서에 포함, 다른 전략은 계속 진행.
   - `OrderSkipError`(최소 수량 미달 등)는 경고 수준으로 처리.

---

## 1-4. 거래소 실제 포지션 대조 (Reconciliation)

### 실행 시점
- `run_pipeline()`·`run_stage2_pipeline()`에서 `setup_exchange()` 직후, **매매 평가 전** (실전 모드만, PAPER는 스킵).

### 대조 로직 (`manager/reconciler.py` 신규)
```python
@dataclass
class ReconcileResult:
    strategy: str          # strategy_A(long) / strategy_B(short)
    state_qty: float
    exchange_qty: float
    state_avg: float
    exchange_avg: float
    status: str            # OK / SYNCED / MISMATCH
    detail: str
```
허용 오차: `tol = max(market 최소수량, state_qty × 1%)`

| 케이스 | 판정 | 조치 |
| :-- | :-- | :-- |
| state 0 / 거래소 0 | `OK` | 정상 진행 |
| 차이 ≤ tol | `SYNCED` | state `total_qty`·`avg_price`를 **거래소 값으로 자동 보정**(반올림 오차 흡수), 로그 기록 |
| state > 0 / 거래소 0 | `MISMATCH` | 수동 청산·강제청산 의심 → **해당 전략 매매 차단** + 경보 |
| state 0 / 거래소 > 0 | `MISMATCH` | 수동 진입·미기록 체결 의심 → 차단 + 경보 |
| 차이 > tol | `MISMATCH` | 차단 + 경보 |
| 포지션 조회 실패 | — | `DataUnavailableError` → 전체 중단 |

- **차단 범위**: MISMATCH 전략만 청산/진입/2단계 감시 모두 스킵, 다른 전략은 정상 진행.
- **경보 쿨다운**: 2단계(5분 주기) 경로는 동일 불일치 경보를 1시간에 1회만 (`state.json`의 `alerts.last_mismatch_alert_at`).
- 일일 보고서에 `🔍 포지션 대조: 롱 OK / 숏 MISMATCH(state 12.3 vs 거래소 0.0)` 행 추가.

### 수동 복구 CLI (`main.py`)
| 명령 | 동작 |
| :-- | :-- |
| `python main.py --reconcile` | 대조 결과만 출력 (변경 없음) |
| `python main.py --reconcile --adopt-exchange` | 거래소 값을 state에 채택. 거래소 0이면 `reset_cycle()`(엑셀에 `SYNC_RESET` 기록), >0이면 qty/avg 덮어쓰기(`SYNC_ADOPT` 기록). **실행 전 확인 프롬프트(y/N)** |

### state.json 손상 보호 (대조의 전제)
- 로드 실패 시 원본을 `state.corrupt_YYYYMMDD_HHMMSS.json`으로 백업.
- 실전 모드: 기본값으로 진행하지 않고 예외 → 중단 + 경보 (복구는 `--reconcile --adopt-exchange`).
- 저장 시 직전 정상본을 `state.bak.json`으로 1부 보관.

---

## 1-5. 2단계 감시 오류 경보
- `run_stage2_pipeline()` 예외 시 현재는 로그만 남음 → 텔레그램 경보 추가 (동일 오류 1시간 쿨다운).

## Phase 1 변경 파일
| 파일 | 변경 |
| :-- | :-- |
| `api/exceptions.py` (신규) | 커스텀 예외 |
| `api/bitget_client.py` | `hedged` 파라미터, `load_markets`, 포지션모드 검증, 최소수량, `OrderResult`·체결 확인, 실전 폴백 제거 |
| `utils/mock_data.py` (신규) | 모의 OHLCV 이동 + 실전 가드 |
| `manager/reconciler.py` (신규) | 포지션 대조 |
| `manager/cycle_manager.py` | 손상 백업/실전 중단, `state.bak.json`, `sync_from_exchange()`, `alerts` 필드 |
| `manager/trader.py` | 주문→체결→기록, `_close_position()` 통합, 전략별 격리, MISMATCH 전략 스킵 |
| `config.py` | `validate_live_config()`, `PAPER_TAKER_FEE`, `RECONCILE_TOLERANCE_PCT=0.01` |
| `main.py` | 설정 검증, `load_ohlcv_or_abort`, 대조 호출, `--reconcile`/`--adopt-exchange`, 2단계 경보 |
| `.gitignore` | `state.bak.json`, `state.corrupt_*.json` |

## Phase 1 검증
1. `python -m py_compile` 전체 모듈.
2. PAPER 드라이런: `PAPER_TRADING=true python main.py --run-once` → 정상 보고.
3. 실전 차단 확인 (주문 미발생 확인용, API 키 임시 제거한 `.env` 사본으로): `PAPER_TRADING=false` + 키 없음 → exit 2 + 경보.
4. `python main.py --reconcile` (실전 키, 읽기 전용) → 롱/숏 OK 확인.
5. **소액 실주문 테스트 (사용자 직접 권장)**: 최소 금액(약 $6)으로 롱 진입→청산, 숏 진입→청산 각 1회 → Bitget 앱에서 헤지 포지션 개설/종료, 엑셀 실체결가·수수료 기록 확인. (테스트용 CLI `--test-order long|short` 추가 — 진입 직후 즉시 청산, state 미반영)

---

# Phase 2 — 전략 로직 개선

## 2-1. 롱 "완만한 하락장 본전탈출" 조건 변경

### 현재 / 변경
[strategy_a_long.py L79-85](../strategy/strategy_a_long.py)
```diff
 if drawdown_rate > DRAWDOWN_THRESHOLD:
     target_price = sma5 * LONG_EXIT_FALLING_SMA_MILD      # 0.99
-    is_met = current_price <= target_price
+    is_met = current_price >= target_price
     action = "IMMEDIATE_EXIT" if is_met else "NONE"
-    reason = f"... SMA5*0.99 목표가(${target_price:,.2f}) 도달 즉시 본탈"
+    reason = f"... SMA5*0.99 목표가(${target_price:,.2f}) 이상 회복 → 즉시 본탈"
```
- 변경 후 롱/숏 6개 조건 모두 "가격이 유리한 방향으로 목표에 도달"하는 대칭 구조가 됩니다.
- `config.py` 주석(L73) 및 PRD L58 "이하" → "이상" 정정.

> [!NOTE]
> **선택 옵션 — 본전 가드** (기본 OFF, 별도 승인 시 적용)
> 변경 후에도 매수가가 SMA5보다 높았던 경우 `SMA5×0.99 회복` 시점이 평단 아래일 수 있습니다. 명칭대로 "본전" 이상만 청산하려면 `current_price >= avg_price × (1 + 2×수수료)` 조건을 AND로 추가(`BREAKEVEN_GUARD_ENABLED`, 숏은 대칭)할 수 있습니다.

---

## 2-2. 동적 자금 `/10` → `/12` 변경 (권장)

### 근거
실제 매수 10회 가중치 합 = 1.0×4 + 1.25×6 = **11.5U**

| 분모 | 10회 완주 시 투입 비율 | 결과 |
| :-- | :-- | :-- |
| `/10` | 115% | 1배 레버리지 한도 초과 → **9회차부터 증거금 부족** |
| `/12` | **95.8%** | 4.2% 버퍼로 수수료·슬리피지 흡수 |

**시뮬레이션** (잔고 $10,000, 1배 롱 DCA, 매수 간 가격 하락률별, 7~10회차 `요청액/가용증거금`):
| 분모 | 매수 간 하락 0% | 매수 간 하락 3% | 매수 간 하락 5% |
| :-- | :-- | :-- | :-- |
| `/10` | 9회차 ✗ (1250/1000) | 9회차 ✗ (1118/241) | 9회차 ✗ (1041/−186) |
| `/12` | 10회차까지 ✓ | 10회차 ✗ (926/653) | 10회차 ✗ (861/209) |

→ `/12`로 9회차까지는 안정적이나, 하락이 깊으면 10회차는 여전히 부족 → **가용증거금 캡**을 함께 적용해 마지막 회차는 "가능한 만큼" 체결.
→ 단점: 1유닛 크기가 16.7% 감소 (회당 수익/손실 규모도 동일 비율 감소).

### 변경 내용
1. `config.py`
   ```python
   UNIT_DIVISOR = 12.0          # 1.0 Unit = 기준 잔고 / 12 (가중치 합 11.5U → 최대 95.8% 투입)
   MAX_EXECUTED_UNITS = 10      # 실제 매수 최대 차수 (진행바·상한 판정용, UNIT_DIVISOR와 분리)
   MARGIN_SAFETY_RATIO = 0.95   # 주문 금액 상한 = 가용증거금 × 0.95
   ```
2. `utils/formatters.py`: `max_units` 기본값을 `int(UNIT_DIVISOR)` → `MAX_EXECUTED_UNITS` (분리하지 않으면 진행바가 12칸으로 바뀜).
3. `trader.py`
   - 하드코딩 `({executed_a}/10)`, `({executed_b}/10)` → `MAX_EXECUTED_UNITS` 참조.
   - `executed_units >= MAX_EXECUTED_UNITS` → 매수 스킵 (`status: SKIPPED_MAX_UNITS`, 보고서 표기).
   - 금액 산정:
     ```python
     target_usd = unit_base_usd * weight
     cap_usd = free_margin * MARGIN_SAFETY_RATIO
     if target_usd > cap_usd:
         logger.warning(f"주문액 ${target_usd:,.2f} → 가용증거금 캡 ${cap_usd:,.2f}")
         target_usd = cap_usd
     if target_usd < MIN_ORDER_USD: → SKIPPED_MIN_ORDER
     ```
   - **실주문 체결 후 잔고 재조회**: A 체결 뒤 B 평가 시 최신 `total_balance/free_margin` 사용 (현재는 파이프라인 시작 시점 값).
4. 문서: `config.py` 주석, `main.py` 보고서 문구, PRD 섹션 2 수식(`/10.0` → `/12.0`), README.

---

## 2-3. 트레일링 보존선 래칫 (유리한 방향으로만 갱신)

### 현재 문제
1. 트레일링 중에도 매일 1차 청산 평가가 다시 실행되어 `set_trailing_mode()`가 `trailing_base_price`를 **새 목표가로 덮어씀** → SMA5 하락 시 보존선도 하향.
2. [trader.py L75-76](../manager/trader.py)에서 가져온 state **사본**을 L199/L348에서 재사용 → 트레일링 **돌입 당일**에는 `trailing_mode=False`로 보여 신규 매수/더미 소진이 실행될 수 있음.

### 변경 규칙
| 상황 | 동작 |
| :-- | :-- |
| 트레일링 비활성 + 1차 `ENTER_STAGE2` | 기존대로 트레일링 활성화 (보존선 = 목표가) |
| 트레일링 활성 + 1차 `ENTER_STAGE2` (새 목표가) | 롱: `new = max(old, 새 목표가)` / 숏: `new = min(old, 새 목표가)` → 개선 시에만 갱신, `trailing_target_price`·사유·갱신일 기록 |
| 트레일링 활성 + 1차 `IMMEDIATE_EXIT` 또는 `NONE` | **무시** (청산은 2단계 감시가 전담, 보존선 유지) |
| 진입 평가 | 반드시 **최신 state 재조회** 후 `trailing_mode` 확인 |

### 구현
```python
# cycle_manager.py
def ratchet_trailing_base(self, strategy_name: str, candidate: float, date_str: str) -> Tuple[bool, float, float]:
    s = self.state[strategy_name]
    old = s.get("trailing_base_price", 0.0)
    is_long = strategy_name == "strategy_A"
    improved = candidate > old if is_long else (old <= 0 or candidate < old)
    if improved:
        s["trailing_base_price"] = candidate
        s["trailing_target_price"] = candidate
        s["trailing_ratcheted_at"] = date_str
        self._save_state(self.state)
    return improved, old, s["trailing_base_price"]
```
- 일일 보고서: `🔼 [롱 보존선 상향] $151.20 → $154.80`.
- 엑셀 이벤트 `TRAIL_RATCHET` 추가 (차트 집계에는 영향 없음).

---

## 2-4. 4H 2단계 판정 기준 — 비교 및 권장안

### 현재 방식의 문제
- 현재가 = **미완성 4H 캔들**의 실시간 가격, SMA5에도 미완성 캔들 포함.
- 5분마다 하루 **288회** 판정 → 3배 레버리지 ETF(SOXL)의 일중 변동으로 4H 추세가 꺾이지 않았는데도 일시적 하향 터치만으로 청산(휩쏘) 가능성이 큼. 특히 4H 캔들 시작 직후에는 미완성봉 가격이 SMA5를 쉽게 교차.

### 옵션 비교
| 옵션 | 추세 이탈 판정 | 장점 | 단점 |
| :-- | :-- | :-- | :-- |
| C. 현행 | 실시간가 < 실시간 SMA5 | 가장 빠른 반응 | 휩쏘 조기청산 多, 추세 수익 단절 |
| B. 연속 2틱 확인 | 10분 연속 이탈 | 단발 스파이크 제거 | 4H 추세 기준으로는 필터 효과 미미 (10분 ≪ 4시간) |
| A. 완성봉 기준 | 직전 **완성** 4H 종가 < 완성봉 SMA5 | 노이즈 대폭 감소, 지표 설계 의도(4H 추세)와 일치 | 최대 4시간 반응 지연 → 고점 대비 이익 일부 반납 |
| **A+ 하이브리드 (권장)** | 추세 이탈 = A(완성봉), **보존선 = 5분 실시간** | 추세는 확정봉으로, 하방 리스크는 실시간 보존선이 즉시 방어 | 급락 시 보존선까지는 반납 가능 (설계상 허용 범위) |

### 권장: **A+ 하이브리드**
- 4H 지연의 최대 위험(급락)은 이미 **실시간 최소 이익 보존선**이 막아주므로, 추세 이탈 판정은 확정봉으로 바꿔 휩쏘를 줄이는 것이 구조적으로 유리합니다.
- 단, 실제 수익 차이는 **백테스트로 검증해야 확정** 가능 → Phase 3에서 4H 과거 데이터 비교 스크립트(`tools/backtest_stage2.py`) 제공 예정, 결과 보고 모드 전환 가능하도록 스위치화.

### 구현
1. `config.py`
   ```python
   STAGE2_SIGNAL_MODE = "closed_candle"   # "closed_candle"(권장 하이브리드) | "realtime"(현행)
   ```
2. `indicator.calculate_4h()`
   - 미완성봉 판별: `timestamp + 4h > now(UTC)` 인 마지막 캔들 제외 → `closed_df`.
   - `closed_close = closed_df.close.iloc[-1]`, `closed_sma5 = closed_df.close.rolling(5).mean().iloc[-1]`, `closed_candle_time` 반환.
   - `realtime_price`(미완성봉 현재가)는 보존선 판정용으로 별도 반환.
   - 최소 캔들: 완성봉 5개 이상 (`CANDLE_LIMIT_4H=30`으로 충분).
3. `strategy_*.check_stage2_exit_signal()`
   - 추세 이탈: 모드에 따라 `closed_close` vs `closed_sma5` 또는 실시간 비교.
   - 보존선: 항상 `realtime_price` 기준 (현행 유지).
   - 사유 문자열에 기준 명시: `"4H 완성봉(13:00) 종가 $150.1 < SMA5 $151.3 (추세 이탈)"`.
4. 트레일링 돌입 이후 **최초 완성봉부터** 판정 (돌입 이전 완성봉의 이탈로 즉시 청산 방지): `trailing_triggered_at` 이후 마감된 봉만 사용.

> [!NOTE]
> Bitget 4H 봉 마감 시각(KST): 01·05·09·13·17·21시. 크론 `3-59/5` 기준 마감 3분 뒤(예: 13:03) 틱에서 완성봉 판정이 이루어집니다.

## Phase 2 변경 파일
| 파일 | 변경 |
| :-- | :-- |
| `strategy/strategy_a_long.py` | 2-1 부등호, 2-4 판정 모드 |
| `strategy/strategy_b_short.py` | 2-4 판정 모드 |
| `strategy/indicator.py` | 2-4 완성봉/실시간 분리 |
| `manager/cycle_manager.py` | 2-3 `ratchet_trailing_base()` |
| `manager/trader.py` | 2-2 최대차수·캡·잔고 재조회, 2-3 래칫·state 재조회 |
| `utils/formatters.py` | 2-2 `MAX_EXECUTED_UNITS` |
| `config.py` | `UNIT_DIVISOR=12.0`, `MAX_EXECUTED_UNITS`, `MARGIN_SAFETY_RATIO`, `STAGE2_SIGNAL_MODE` |
| `utils/excel_logger.py` | `TRAIL_RATCHET` 색상 |
| `docs/prd/bitget_v3_switch_prd.md` | 롱 MILD 조건, `/12`, 래칫, 4H 판정 규칙 반영 |

## Phase 2 검증
- `py_compile` 전체.
- 단위 테스트(신규 `tests/`): 롱 MILD `>=` 경계값, 래칫(상향만 반영·하향 무시·IMMEDIATE_EXIT 무시), 돌입 당일 진입 스킵, `MAX_EXECUTED_UNITS`·가용증거금 캡, 미완성 4H 봉 제외·돌입 이전 봉 무시.
- PAPER `--run-once` / `--stage2` 실행 로그 확인.

---

# Phase 3 — 운영 안정성 및 코드 품질

| ID | 내용 | 비고 |
| :-- | :-- | :-- |
| 3-1 | README Crontab 표기 `*/5` → `3-59/5` 정정 + 이유(09:10~09:15 윈도우 1회 충족) 명시 | `*/5`로 재배포 시 하루 2회 평가 위험 예방 |
| 3-2 | (선택) 일봉 평가 멱등성 가드 `last_evaluated_candle` | 크론 설정 실수·`--loop` 모드 대비 저비용 보험 |
| 3-3 | (선택) 프로세스 락 `run.lock` | 수동 실행과 크론 동시 실행 방지 |
| 3-4 | 엑셀 파일 잠금(`PermissionError`) 시 3회 재시도 → `logs/trade_history_fallback.csv` 백업 | 엑셀을 열어둔 상태 대비 |
| 3-5 | `trader.py` A/B 중복 제거 (`SideConfig` 기반 공통화) | Phase 1의 `_close_position()` 확장 |
| 3-6 | 미사용 import/변수, 레거시 `check_exit_signal` 정리 | |
| 3-7 | PAPER 잔고에 누적 실현손익 반영 | 모의 성과 현실화 |
| 3-8 | `tools/backtest_stage2.py` — 4H realtime vs closed_candle 성과 비교 | 2-4 모드 최종 결정용 |
| 3-9 | pytest 정식 도입 (`requirements-dev.txt`) | |

---

## 사용자 확인 사항
1. **2-1 본전 가드 옵션**(평단+수수료 이상에서만 본탈 청산)을 함께 적용할까요? (기본: 미적용)
2. **2-2 `MARGIN_SAFETY_RATIO = 0.95`**, `MAX_EXECUTED_UNITS = 10` 값이 적절한가요?
3. **2-4** 권장안(하이브리드, `closed_candle`)을 기본값으로 설정해도 될까요?
4. **Phase 1-4** 차이가 허용오차 이내일 때 state를 거래소 값으로 **자동 보정**하는 방식에 동의하시나요? (대안: 보정 없이 경보만)

준비되면 `/apply Phase 1`로 시작해 주세요.
