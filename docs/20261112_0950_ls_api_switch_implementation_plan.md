# v3-switch 일봉 신호 소스 LS(미국 현물)로 전환 — 봇 구현 계획서

> **작성일시:** 2026-11-12 09:50 KST  
> **대상 폴더:** `week_00-bitget_v3_switch` (EC2 배포 경로 `/var/autobot/bitget_v3_switch`)  
> **원본 분석:** `appsscript/00_dashboard/.plan/2026-11-12_0846_v3switch_ls_api_revised.md`  
> **짝 계획서(대시보드):** `appsscript/00_dashboard/.plan/2026-11-12_0950_v3switch_dashboard_snapshot.md`  
> **실행:** 이 폴더에서 `/apply` 시 본 계획서 기준으로 반영. 자동 커밋/푸시 금지.

---

## 0. 목표와 범위

- 일봉 신호(시가·종가·SMA5·SMA60·캔들 등락률·전일비·낙폭/반등)를 **Bitget 1D 선물 캔들 → 미국 SOXL 현물 일봉(LS 공통모듈, `yf_auto`)** 으로 교체합니다. 떨사오팔과 같은 "신호=미국 현물 / 주문=Bitget 선물" 구조입니다.
- 일봉 평가 시각을 **09:10 KST → 미국장 마감 직전(15:54~15:59 ET)** 으로 옮깁니다.
- **변경하지 않는 것:** 2단계 1시간봉 5MA 감시(`run_stage2_pipeline`, `sync_4h_trend_state`), Bitget 주문/포지션 대조, 유닛·더미 로직, 진입/청산 임계치 수치.

---

## 1. 원본 분석서 대비 정정 사항 (실제 코드 확인 결과)

| 항목 | 원본 분석서 | 실제 확인 결과 → 본 계획 |
|---|---|---|
| OHLCV 함수 | `LsCommon.GetOhlcv("US", "SOXL")` | `LS_Common_new.GetOhlcv`는 **국내(t8410) 전용**입니다(`LS_API_Helper_US_new.py` 1080~1083행 주석). 떨사오팔은 `LsCommon.GetOhlcv`를 `LsiUS.GetOhlcv(source="yf_auto")` 래퍼로 **덮어써서** 쓰고 있습니다(`bitget_Ejftkdhvkf.py` 695~826행). → v3-switch는 **`LsiUS.GetOhlcv("US", "SOXL", limit, source="yf_auto")`를 직접 호출**합니다. |
| 마지막 봉 의미 | `iloc[-1]` = 어제 완성봉 | 장중(15:5x ET)에 조회하면 **`iloc[-1]` = 오늘 진행 중인 봉**입니다(`_IsUsHoliday`가 "마지막 봉 날짜 == 오늘 ET"로 개장 판정, 1086~1099행). 떨사오팔도 `ohlcv["open"].iloc[-1]`을 **당일 시가**로 씁니다. → 2장 참고 |
| 실행 시각 | 15:54 ET | 크론 표현식이 `3-59/5`(main.py 문서) 또는 `*/5`(README)라 15:54 정각 틱이 없습니다. → **15:54~15:59 ET 구간**으로 정의(3-59/5면 15:58, */5면 15:55에 실행) |
| 반환 형식 | — | DataFrame 인덱스=날짜, 컬럼=`open/high/low/close/volume/...`. **`datetime`·`timestamp` 컬럼이 없음** → 어댑터에서 생성 |

---

## 2. 판정 캔들 정의 (핵심 결정)

기존 로직의 의미: 09:10 KST에 "방금 마감된 Bitget 일봉(UTC 00:00~24:00, 전날 미국 정규장 전체 포함)"을 판정했습니다.

**채택안 (A): 장마감 직전 당일 봉 판정**

15:5x ET에 조회한 `iloc[-1]`(오늘 정규장, 마감 2~6분 전)을 판정 봉으로 씁니다. 기존 "방금 끝난 미국 세션의 봉을 판정한다"는 의미를 그대로 유지하면서 판정 시점이 약 13시간 앞당겨집니다.

| 지표 | 기존 (Bitget, 09:10 KST) | 변경 (미국 현물, 15:5x ET) |
|---|---|---|
| `eval_open` | `open.iloc[-2]` | `open.iloc[-1]` (당일 시가, `yf_auto`로 시가 오차 회피) |
| `eval_close` | `close.iloc[-2]` | `LsiUS.GetCurrentPrice("SOXL")` (조회 실패 시 `close.iloc[-1]`) |
| `prev_eval_close` | `close.iloc[-3]` | `close.iloc[-2]` (전일 종가) |
| `sma5`, `sma60` | 완성봉 기준 `iloc[-2]` | `close.iloc[-1]`을 `eval_close`로 치환한 시리즈의 `iloc[-1]` |
| `sma60_prev` | `iloc[-3]` | `iloc[-2]` |
| `candle_date` | `"YYYY-MM-DD 09:00:00"` (KST) | `"YYYY-MM-DD"` (**ET 거래일**, `iloc[-1]` 인덱스 날짜) |

**안전 가드:** `iloc[-1]`의 날짜가 오늘(ET)이 아니면 휴장 또는 데이터 지연으로 보고 평가를 건너뜁니다(텔레그램 경고 1회).

> 대안 (B): 전일 완성봉(`iloc[-2]`) 판정 — 데이터는 확정적이지만 신호가 하루 늦어져 기존 대비 체감 지연이 커서 채택하지 않습니다.

---

## 3. 단계별 구현

### 3-0. 사전 보안 조치 (최우선)

`.gitignore`에 LS 키·토큰이 빠져 있습니다. 로컬 폴더에 `myLS_StockInfo.yaml`, `ls_real_token.json`이 있으므로 커밋 전에 추가합니다.

```gitignore
# LS 증권 API 키·토큰
myLS_StockInfo.yaml
ls_*_token.json
```

이미 추적 중이면 `git rm --cached myLS_StockInfo.yaml ls_real_token.json`도 수행합니다(`/git-commit` 시점에 사용자 확인 후).

### 3-1. `config.py`

```python
# 일봉 신호 소스
DAILY_SIGNAL_SOURCE = os.getenv("DAILY_SIGNAL_SOURCE", "ls")   # "ls"(미국 현물, 기본) | "bitget"(기존 방식 롤백용)
US_SYMBOL = "SOXL"
LS_OHLCV_LIMIT = 120                                            # SMA60 + 여유
LS_CHANGE_MODE = "REAL"                                         # myLS_StockInfo.yaml REAL_APP_KEY 접두사와 일치

# 일봉 평가 시각 (미국 동부시간, 서머타임 자동)
DAILY_EVAL_ET_HOUR = 15
DAILY_EVAL_ET_START_MIN = 54
DAILY_EVAL_ET_END_MIN = 59
```

- 기존 `DAILY_EVAL_HOUR_KST`, `DAILY_EVAL_START_MIN`, `DAILY_EVAL_END_MIN`, `WEEKEND_FREEZE_DAYS`는 `DAILY_SIGNAL_SOURCE == "bitget"` 롤백 경로에서만 사용하도록 남겨 둡니다.
- `requirements.txt`에 `pyyaml`, `pytz`, `yfinance` 추가(LS 공통모듈 의존성: `LS_Common_new.py` 37·385행, `yf_auto` 소스).

### 3-2. 신규 `api/ls_client.py` (LS 어댑터)

- 모듈 상단에서 `import LS_Common_new as LsCommon`, `import LS_API_Helper_US_new as LsiUS` 후 `LsCommon.SetChangeMode(LS_CHANGE_MODE)` 1회.
  - `main.py`가 봇 루트에 있어 `sys.path[0]`이 루트이므로 하위 패키지에서도 import 가능합니다.
- `fetch_daily_ohlcv(limit) -> pd.DataFrame`
  - `LsiUS.GetOhlcv("US", US_SYMBOL, limit, source="yf_auto")` 호출, 최대 3회 재시도(떨사오팔 `_GetOhlcv_with_retry`의 유효성 체크 이식: 빈 DF, `open`/`close` 컬럼, 마지막 시가 비정상값).
  - 인덱스 날짜로 `datetime` 컬럼(`"YYYY-MM-DD"`)과 `timestamp` 컬럼(ms) 생성 → 기존 `IndicatorCalculator` 인터페이스 호환.
- `fetch_current_price() -> float | None`: `LsiUS.GetCurrentPrice(US_SYMBOL)`, 실패 시 None.
- `is_us_market_open() -> bool`: `LsiUS.IsMarketOpen(check_holiday=True)`.
- 모든 예외는 `DataUnavailableError`로 감싸 던집니다(기존 예외 체계 유지).

### 3-3. `strategy/indicator.py`

- `IndicatorCalculator.calculate(df, cycle_peak, cycle_trough, mode="bitget_closed" | "us_intraday_close", live_price=None)`로 판정 봉 위치를 분기합니다.
  - `bitget_closed`: 기존 로직 그대로(롤백용).
  - `us_intraday_close`: 2장 표대로 `iloc[-1]` 판정, `live_price`가 있으면 마지막 종가를 치환한 뒤 SMA 계산.
- 반환 딕셔너리에 `signal_price`(판정용 현물가)를 추가하고, `candle_date`는 ET 날짜 문자열로.
- 로그 문구 "기준완성봉"은 모드에 따라 "기준봉(당일 장마감 직전)"으로 표기.

### 3-4. `main.py`

**① 데이터 수집** — `run_pipeline()` 2단계:
```python
if DAILY_SIGNAL_SOURCE == "ls":
    df = ls_client.fetch_daily_ohlcv(LS_OHLCV_LIMIT)          # 실패 시 DataUnavailableError
    live = ls_client.fetch_current_price()
    indicators = IndicatorCalculator.calculate(df, ..., mode="us_intraday_close", live_price=live)
    indicators["exec_price"] = bitget_client.fetch_last_price()  # 주문 기준가 (Bitget 선물)
else:
    (기존 Bitget 1D 경로)
```
- 실전 모드에서 LS 실패 시 **Bitget으로 자동 대체하지 않고** 중단 + 경보합니다(신호 소스가 섞이면 SMA 기준이 바뀌어 청산가가 튑니다). 롤백은 `DAILY_SIGNAL_SOURCE=bitget`으로 명시적으로만.

**② 평가 시간 판정** — `is_daily_evaluation_window()`를 ET 기준으로:
```python
et_now = datetime.now(ZoneInfo("America/New_York"))
return et_now.weekday() < 5 and et_now.hour == DAILY_EVAL_ET_HOUR and DAILY_EVAL_ET_START_MIN <= et_now.minute <= DAILY_EVAL_ET_END_MIN
```

**③ 주말·휴장 동결** — KST 일/월 동결(`WEEKEND_FREEZE_DAYS`) 대신 ET 평일 판정 + `ls_client.is_us_market_open()` 휴장 판정. `run_pipeline()` 0단계와 `run_cron_tick()` 양쪽 모두 교체.

**④ 중복 가드** — `run_cron_tick()`의 `expected_eval_candle`을 `et_now.strftime("%Y-%m-%d")`로 변경(기존 `"(KST-1일) 09:00:00"` 형식 대체).

**⑤ 텔레그램 일봉 보고** — "일봉 변동률" 줄에 기준 표기 추가: `(미국 SOXL 현물 · 11/12 ET 장마감 직전)`. "실시간 현재가"는 `signal_price`와 `exec_price`(Bitget)를 함께 표기.

**⑥ 문서 문자열** — 파일 상단 docstring과 `run_cron_tick` 주석의 "09:10 KST" 표기를 "15:54~15:59 ET"로 수정.

### 3-5. `manager/trader.py` — 가격 역할 분리

`indicators['current_price']`가 현재 판정·주문 모두에 쓰입니다(164·262·307·367·427·480·1024·1030행 등).

| 용도 | 사용할 가격 |
|---|---|
| 1차 청산 목표가 비교(`SMA5 × 배수` 대비), 싸이클 고점/저점 갱신(`update_price_extremes`), 진입 신호 판정 | `signal_price` (미국 현물) |
| 주문 수량 산출(`target_usd / price`), `ref_price`, 최소 주문금액 검사, 체결가 미확인 시 대체값 | `exec_price` (Bitget 선물) |

- 구현: `process_daily_cycle` 진입 시 `signal_px = indicators.get('signal_price', indicators['current_price'])`, `exec_px = indicators.get('exec_price') or signal_px`로 분리해 각 위치에 전달.
- 2단계 감시(`process_stage2_monitoring`, 1111행 이후)는 Bitget 1시간봉 그대로 → 변경 없음.
- `api/bitget_client.py`에 `fetch_last_price(symbol=None) -> float` 메서드 신규 추가(`self.exchange.fetch_ticker(symbol)['last']`, PAPER 모드에서는 모의 가격 또는 `signal_price` 반환). 현재는 `main.py` 500행 `run_test_order`에서만 `fetch_ticker`를 직접 호출합니다.

### 3-6. `manager/cycle_manager.py` — 대시보드 스냅샷

- `state.json`에 최상위 키 `daily_indicators` 추가(일봉 평가 시 1회 갱신). 신규 메서드 `update_daily_indicators(ind: dict)`.
- `_build_sync_snapshot()`(175~183행)에서 `trend_riding`과 같은 방식으로 `snap["daily_indicators"] = state.get("daily_indicators")` 포함 → 기존 `_StateSyncPusher`가 자동 전송.

```python
self.state["daily_indicators"] = {
    "source": "LS",                       # LS(yf_auto 경유 포함) | bitget(롤백 모드)
    "candle_date": "2026-11-12",          # 판정한 미국 거래일(ET)
    "evaluated_at_kst": "2026-11-13 05:58:10",
    "eval_open": 31.20, "eval_close": 30.55, "prev_close": 31.80,
    "candle_change_pct": -2.08, "daily_change_pct": -3.93,
    "sma5": 31.42, "sma60": 28.90, "prev_sma60": 28.85,
    "has_sma60": True, "is_sma60_rising": True,
    "high40": 38.10, "low40": 24.70,
    "dd_from_high_pct": -19.8, "rebound_from_low_pct": 23.7,
    "long_target_mult": 1.02, "short_target_mult": 0.98,
    "long_exit_rule": "일봉 5MA +2.0% 이상 장마감 시 50% 선익절 + 2단계 승격 (60일선 상승)",
    "short_exit_rule": "일봉 5MA -2.0% 이하 장마감 시 50% 선익절 + 2단계 승격 (60일선 상승)"
}
```

- 목표가 배수·문구는 기존 청산 배수 결정 로직(`LONG_EXIT_*`, `SHORT_EXIT_*` 분기)의 결과를 그대로 기록합니다.
- 숫자 필드만 담고 캔들 배열은 넣지 않습니다(대시보드 PropertiesService 값당 9KB 제한).
- `dummy_history` 항목은 이미 `date`·`price`를 가지고 있으므로(`consume_dummy` 340~360행) `date`가 ET 거래일로 바뀌는 것 외에 구조 변경 없음. `price`에는 `signal_price`를 기록.
- `last_daily_eval_candle`은 ET 날짜 형식으로 저장. 전환 첫날 기존 값(`"... 09:00:00"`)과 형식이 달라 중복 가드에 걸리지 않으므로 별도 마이그레이션 불필요.

### 3-7. `README.md`

- 크론 예시를 실제 서버 기준으로 수정:
  ```cron
  3-59/5 * * * * cd /var/autobot/bitget_v3_switch && /usr/bin/python3 -u main.py --cron >> /var/autobot/bitget_v3_switch/logs/cron.log 2>&1
  ```
- 데이터 흐름(신호=미국 현물 / 주문·2단계=Bitget)과 LS 설정 파일 링크 요구사항(4장) 기술.

---

## 4. 서버 배포 환경 (LS 공통모듈 = 떨사오팔판)

### 4-1. 설정 파일 경로 제약

`LS_Common_new.py`는 `_SETTING_FILE = "./myLS_StockInfo.yaml"`(88행), 토큰은 `./ls_{dist}_token.json`(173행)으로 **현재 작업 폴더 기준 상대경로**를 씁니다. 크론은 `cd /var/autobot/bitget_v3_switch` 후 실행되므로 봇 루트에 yaml이 있어야 합니다.

### 4-2. 심볼릭 링크 (필수)

```bash
ln -s /home/ec2-user/myLS_StockInfo.yaml /var/autobot/bitget_v3_switch/myLS_StockInfo.yaml
chmod 600 /home/ec2-user/myLS_StockInfo.yaml
```

- 로컬 폴더의 `myLS_StockInfo.yaml`·`ls_real_token.json`은 **서버로 업로드하지 않습니다**(링크 덮어쓰기 방지). 배포 시 제외 목록에 추가.
- 수동 실행(`--run-once`, `--force`, `--stage2`, `--reconcile`)도 반드시 `cd /var/autobot/bitget_v3_switch` 후 실행.
- 크론은 yaml을 읽을 수 있는 `ec2-user` crontab에 등록.

### 4-3. 토큰 파일

- 봇 루트에 v3-switch 전용 `ls_real_token.json`이 생성됩니다(떨사오팔과 별도 파일).
- 같은 앱키로 각자 발급하므로 실행 시각을 15:51(떨사오팔) / 15:52(장기투자) / 15:55 또는 15:58(v3-switch)로 분리해 동시 재발급을 피합니다.

### 4-4. 배포 전 서버 점검

```bash
cd /var/autobot/bitget_v3_switch
ls -l myLS_StockInfo.yaml                     # -> /home/ec2-user/myLS_StockInfo.yaml
python3 -c "import LS_Common_new as c; c.SetChangeMode('REAL'); import LS_API_Helper_US_new as u; print('현재가', u.GetCurrentPrice('SOXL')); print(u.GetOhlcv('US','SOXL',5,source='yf_auto').tail(3)); print('장중', u.IsMarketOpen())"
ls -l ls_*token*.json
PAPER_TRADING=true python3 main.py --run-once --force
```

마지막 명령으로 `[Indicators]` 로그의 기준봉 날짜(ET), SMA5/SMA60, `state.json`의 `daily_indicators.source == "LS"`를 확인합니다.

---

## 5. 배포 순서

1. 로컬 반영 → `python -m py_compile` 검증
2. 서버 업로드(키·토큰 파일 제외) → 4-2 링크 → 4-4 점검(PAPER)
3. 실전 전환 후 첫 평가일 15:5x ET 로그 확인
4. 대시보드 계획서(`2026-11-12_0950_v3switch_dashboard_snapshot.md`) 반영

---

## 6. 검증

- `python -m py_compile main.py config.py api/ls_client.py strategy/indicator.py manager/cycle_manager.py manager/trader.py`
- PAPER 모드 `--run-once --force` 1회: 판정 봉 날짜, SMA, 진입/청산 판정, `exec_price`(Bitget)로 주문 수량 산출 로그 확인
- 전환 첫 주: 텔레그램 일봉 보고의 SMA5·목표가와 대시보드 표시값 매일 대조

## 7. 롤백

`.env`에 `DAILY_SIGNAL_SOURCE=bitget` 설정 → 기존 09:10 KST Bitget 1D 경로로 즉시 복귀(코드 변경 불필요). 이 경우 `daily_indicators.source = "bitget"`으로 기록되어 대시보드에도 표시됩니다.
