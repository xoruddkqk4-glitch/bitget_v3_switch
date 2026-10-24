# 매매 로직 검증 결과 및 수정 계획서 (2026-10-24 13:55 KST)

> 상태: **계획 단계 (코드 미수정)** — `/apply` 또는 명시적 승인 후 반영합니다.

## 0. 검증 범위와 방법

- 대상: `main.py`, `config.py`, `manager/trader.py`, `manager/cycle_manager.py`, `manager/reconciler.py`,
  `strategy/indicator.py`, `strategy/strategy_a_long.py`, `strategy/strategy_b_short.py`, `api/bitget_client.py`
- 정적 검증: 전체 18개 `.py` 파일 `python -m py_compile` → **모두 통과**
- 동작 재현: `PAPER_TRADING=true`로 임시 스크립트를 실행해 아래 1-1, 1-5, 1-6을 재현 (소스·state 미변경)

### 정상 동작 확인 항목

| 항목 | 결과 |
| --- | --- |
| 진입 신호 (롱 음봉 ≤ -1.5% 또는 전일비 ≤ -3%, 숏 대칭) | PRD와 일치, 완성봉(`iloc[-2]`) 기준 |
| 1차 청산 목표가 (SMA5 × 1.02 / 0.99 / 1.03, 숏 대칭) | PRD와 일치 |
| 매도 우선 → 당일 청산 시 1차 더미 이관 | 정상 |
| 더미 2회 → 실전 1.0x(1~4회차) → 1.25x(5회차~) → 최대 회차 스킵 | 정상 |
| 상대 전략 최초 실전 매수 시 미완성 더미 리셋 | 정상 |
| 2단계 보존선 래칫(롱 상향/숏 하향만), 4H 완성봉·돌입 이후 봉만 인정 | 정상 |
| 실전 모드에서 모의 데이터/잔고 대체 금지, 대조 불일치 전략 차단 | 정상 |
| 체결 확인(`OrderResult`) 기준 상태·손익 기록 | 정상 |

---

## 1. 발견된 문제

### 1-1. [P0] 일봉 평가 하루 중복 실행 방지 장치 없음

- 위치: `main.py` `is_daily_evaluation_window()`, `run_cron_tick()`, `run_pipeline()`
- 평가 창이 09:10~09:15(6분)인데, `--loop` 모드는 `time.sleep(300)`으로 정렬 없이 돌기 때문에
  09:10:30 / 09:15:30처럼 **같은 날 두 번** 창에 들어갈 수 있습니다 (재현 확인).
- 두 번째 실행은 같은 완성봉으로 다시 평가하므로 **더미 이중 소진 또는 실전 매수 이중 집행**이 발생합니다.
- 크론 실행 중 `--run-once`를 수동으로 실행해도 똑같이 중복됩니다.
- 반대로 크론(`3-59/5`)은 창 안에 09:13 한 번뿐이라, 그 틱이 데이터 수집 오류로 실패하면 **그날 평가는 재시도 없이 누락**됩니다.

### 1-2. [P0] 2단계 감시 중 잔고 조회 실패 시 청산 자체가 중단

- 위치: `manager/trader.py` `process_stage2_monitoring()` 790행 `self._refresh_balance()`
- 잔고는 엑셀·알림 표시용인데, 실전에서 잔고 API가 실패하면 예외가 전파되어
  **보존선 이탈 상황에서도 청산을 하지 못합니다.**

### 1-3. [P1] `--loop` 모드에서 state.json을 다시 읽지 않음

- 위치: `main.py` `--loop` 분기, `cycle_manager.reload()`(정의만 있고 호출 없음)
- 상시 루프가 메모리 상태를 계속 쓰므로, 루프 실행 중 `--reconcile --adopt-exchange`로 디스크를 고쳐도
  반영되지 않고 다음 저장 때 **복구 내용이 덮어써집니다.** 시작 시 손상 플래그(`load_error`)도 영구 유지됩니다.

### 1-4. [P1] 부분 청산된 날 같은 방향 추가 매수 가능

- 위치: `trader.py` `_evaluate_stage1_exit()` → `_close_position()` 부분 체결 시 `False` 반환
- 즉시 청산(`IMMEDIATE_EXIT`)이 부분 체결되면 `cleared_today=False`로 처리되어 진입 평가가 그대로 진행되고,
  진입 신호가 있으면 **청산 중인 포지션에 유닛을 추가 매수**합니다.

### 1-5. [P1] 일일 보고서 "포지션 동기화"가 불일치인데 "정상"으로 표시

- 위치: `main.py` 285행 `"OK" in sync_status`
- 요약 문자열에 한쪽이라도 `OK`가 있으면 정상으로 판정합니다.
  재현: `"롱 ⛔MISMATCH(...) / 숏 OK"` → `True` → "정상 (일치)"로 표시됨.

### 1-6. [P2] 유닛 분모(15)보다 최대 소요량(15.25 Unit)이 큼

- 위치: `config.py` `UNIT_DIVISOR = 15.0`, `MAX_EXECUTED_UNITS = 13`
- 소요량: 1.0 × 4 + 1.25 × 9 = **15.25 Unit** → 마지막 회차는 가용 잔고 상한(`MARGIN_SAFETY_RATIO`)에 걸려 축소되거나,
  헤지 상태에서는 최소 주문금액 미만으로 스킵될 수 있습니다.
- PRD는 여전히 `12 / 10회차`로 적혀 있어 문서와 코드도 불일치합니다.

### 1-7. [P2] 청산 직후 잔고를 갱신하지 않아 반대편 진입 유닛이 과소 산정

- 위치: `trader.py` `_close_position()` 전량 청산 후
- 청산으로 늘어난 가용 잔고가 반영되지 않은 채 다른 전략의 헤지 유닛(가용 잔고 / 15)이 계산됩니다. (보수적 방향이라 손실 위험은 낮음)

### 1-8. [P3] 숏 전략에 본전 가드 옵션 미적용

- `BREAKEVEN_GUARD_ENABLED`는 롱에만 적용됩니다. 현재 기본값 `False`라 영향 없음.

### 참고: 설계 의도 확인이 필요한 동작 (수정 대상 아님)

- 2단계 돌입 시 보존선 = max(목표가, 돌입 시점 현재가)이므로 **돌입가 대비 -0.2%만 빠져도 청산**됩니다.
  (재현: 목표 102, 돌입가 105 → 104.79에서 청산) ✅ 사용자 확인: **의도한 동작으로 유지**
- `cycle_peak`/`cycle_trough`는 하루 1회(09:13 현재가)만 갱신되고 장중 고가/저가는 반영하지 않습니다.

---

## 2. 수정 계획

### Step 1 — 일봉 평가 1일 1회 가드 + 재시도 가능화 (1-1)

- `cycle_manager.py`
  - 최상위 state에 `last_daily_eval_candle`(평가한 완성봉 `candle_date`) 필드 추가, `_load_state`에서 기본값 보정
  - `is_daily_evaluated(candle_date)`, `mark_daily_evaluated(candle_date)` 메서드 추가
- `main.py` `run_pipeline(force=False)`
  - 지표 산출 직후 `indicators['candle_date']`가 이미 처리된 값이면 **매매 없이 종료**(로그만)
  - `trader.process_daily_cycle()` 정상 반환 직후 `mark_daily_evaluated()` 기록 (보고서 발송 실패와 무관하게 중복 방지)
  - `--run-once`는 기본적으로 가드 적용, `--force` 옵션 추가 시에만 재평가 허용
- `config.py` `DAILY_EVAL_END_MIN`을 15 → 30으로 확대 (크론 09:13/18/23/28 재시도 가능, 가드로 중복 차단)

### Step 2 — 2단계 감시 잔고 조회 실패 격리 (1-2)

- `trader.py` `process_stage2_monitoring()`의 `_refresh_balance()`를 try/except로 감싸고 실패 시 경고 후 직전 값 유지

### Step 3 — `--loop` 틱마다 state 재로드 (1-3)

- `main.py` `--loop` 분기에서 각 `run_cron_tick()` 전에 `cycle_manager.reload()` 호출

### Step 4 — 부분 청산 당일 진입 차단 (1-4)

- `trader.py` `process_daily_cycle()`: `_close_position()`이 부분 체결을 반환한 전략은 `failed`와 같이 진입 평가에서 제외하고
  `last_eval`에 "부분 청산 진행 중 (진입 스킵)" 기록

### Step 5 — 동기화 요약 판정 수정 (1-5)

- `main.py`: 문자열 검사 대신 `all(r.status in ("OK", "SYNCED") for r in recon_results.values())`로 판정

### Step 6 — 유닛 분모/최대 회차 정합성 (1-6) ✅ 사용자 결정: 안 A

- `config.py` `UNIT_DIVISOR = 16.0` (13회차 유지, 15.25 Unit 소요 → 0.75 Unit 여유), 주석 근거 갱신
- PRD 2장(분모 12 → 16, 최대 10 → 13회차, 근거 수식)·4장 트리 주석 수치 갱신
- 현재 롱 실전 4회차 진행 중 → 다음 매수부터 유닛 금액 약 6% 감소 적용

### Step 7 — 청산 후 잔고 재조회 (1-7)

- `trader.py` `_close_position()` 전량 청산 후 `_refresh_balance()`를 try/except로 호출

### Step 8 — (선택) 숏 본전 가드 대칭 적용 (1-8)

- `strategy_b_short.py` 완만한 반등 즉시 청산 분기에 `avg_price × (1 - BREAKEVEN_GUARD_MIN_PCT)` 가드 추가

---

## 3. 검증 계획

1. 수정 파일 `python -m py_compile`
2. `PAPER_TRADING=true` + 임시 state 파일로 오프라인 시나리오 스크립트 실행 (실제 state.json·엑셀 미변경)
   - 같은 `candle_date`로 `run_pipeline()` 2회 → 두 번째는 매매 없이 종료되는지
   - 2단계 감시에서 잔고 조회 예외를 주입해도 보존선 이탈 청산이 집행되는지
   - 부분 체결 결과를 주입했을 때 같은 날 진입이 스킵되는지
   - MISMATCH + OK 조합에서 보고서 동기화 줄이 불일치로 표시되는지
3. 커밋/푸시는 `/git-commit` 명령 시에만 진행
