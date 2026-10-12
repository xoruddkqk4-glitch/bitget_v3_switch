# Phase 1 실행 계획서 — 실전 안전장치 (LLM 즉시 실행용)

- 작성 일시: 2026-10-12 20:30 (KST)
- 근거 문서: [v2 계획서](./20261012_2013_code_review_implementation_plan_v2.md) Phase 1 (1-1 ~ 1-5)
- 후속 문서: [Phase 2](./20261012_2030_phase2_implementation_plan.md) → [Phase 3](./20261012_2030_phase3_implementation_plan.md) (**반드시 Phase 1 완료 후 순서대로 적용**)
- 기준 코드: 2026-10-12 20:30 시점 워킹트리 (커밋 `1c37558` 이후 미커밋 변경 없음 가정)

---

## 0. 실행 규칙 (실행 LLM 필독)

1. **Step 순서대로** 적용합니다. 뒤 Step은 앞 Step에서 만든 심볼(예외 클래스, 설정 상수, 메서드)에 의존합니다.
2. "**전체 교체**"로 표시된 파일은 기존 내용을 지우고 제시된 코드로 덮어씁니다. "**Diff**"로 표시된 파일은 제시된 위치만 수정하고 나머지는 그대로 둡니다.
3. Diff의 줄 번호는 **수정 전 원본 기준**입니다. 줄 번호가 다르면 함께 제시된 **앵커 코드(정확한 기존 코드)** 로 위치를 찾습니다.
4. 각 Step 끝의 **Step 검증**을 통과한 뒤 다음 Step으로 넘어갑니다.
5. 계획서에 없는 리팩토링·포맷팅·주석 삭제를 하지 않습니다. Git 커밋/푸시 금지 (AGENTS.md Rule 3/4).
6. 실행 셸은 Windows PowerShell, 작업 디렉터리는 프로젝트 루트(`week_00-bitget_v3_switch`)입니다.

## 0-1. 기본 결정값 (사용자 미확정 항목 — 설정 상수로 노출)

| 항목 | 기본값 | 위치 |
| :-- | :-- | :-- |
| 허용 오차 이내 포지션 차이 자동 보정 | `RECONCILE_AUTO_SYNC = True` | `config.py` |
| 포지션 대조 허용 오차 | `RECONCILE_TOLERANCE_PCT = 0.01` (1%) | `config.py` |
| 부분 체결 판정 비율 | `PARTIAL_FILL_RATIO = 0.99` | `config.py` |
| 수수료 추정치 (PAPER 체결·체결확인 폴백) | `TAKER_FEE_RATE = 0.0006` | `config.py` |
| 경보 재발송 쿨다운 | `ALERT_COOLDOWN_SEC = 3600` | `config.py` |

## 0-2. 변경 파일 및 의존 순서

| Step | 파일 | 방식 | 의존 |
| :-- | :-- | :-- | :-- |
| 1 | `api/exceptions.py` | 신규 | — |
| 2 | `config.py` | Diff | — |
| 3 | `utils/mock_data.py` | 신규 | Step 2 |
| 4 | `utils/alert_throttle.py` | 신규 | Step 2 |
| 5 | `api/bitget_client.py` | 전체 교체 | Step 1, 2 |
| 6 | `manager/cycle_manager.py` | Diff | Step 2 |
| 7 | `utils/excel_logger.py` | Diff | — |
| 8 | `manager/reconciler.py` | 신규 | Step 2, 5, 6, 7 |
| 9 | `manager/trader.py` | 전체 교체 | Step 1, 2, 4, 5, 6 |
| 10 | `main.py` | 전체 교체 | Step 1~9 |
| 11 | `.gitignore` | Diff | — |
| 12 | 통합 검증 | — | 전체 |

**동작 변경 요약** (기존 대비): ① 헤지모드 주문 파라미터 `hedged=True` ② 실전에서 모의 데이터·모의 잔고·모의 주문 완전 차단 ③ 주문 → 체결 확인 → 기록 순서 ④ 포지션 대조 + 불일치 전략 매매 차단 ⑤ 트레일링 돌입 당일 신규 진입 차단(최신 state 재조회) ⑥ 실주문 체결 후 잔고 재조회 ⑦ 당일 청산 후 더미 이관 시 `cycle_id` 이중 증가 버그 수정 ⑧ 2단계 청산 기록 일시를 실제 청산 시각(KST)으로 변경.

---

## Step 1. `api/exceptions.py` (신규)

### (1) 파일 경로
`api/exceptions.py`

### (2) 기존 코드 영역
없음 (신규 파일)

### (3) 코드
```python
"""
api/exceptions.py
자동매매 시스템 커스텀 예외 정의
"""


class TradingError(Exception):
    """자동매매 시스템 공통 예외 베이스 (실전 매매 중단 경보 대상)"""


class ExchangeConfigError(TradingError):
    """거래소 계정 설정(포지션 모드/마진 모드/레버리지/계약 단위)이 기대값과 다를 때"""


class DataUnavailableError(TradingError):
    """실전 모드에서 시세/잔고/포지션 데이터를 신뢰할 수 없을 때 (모의 데이터 대체 금지)"""


class StateCorruptedError(TradingError):
    """state.json 손상으로 실전 매매를 계속할 수 없을 때"""


class OrderSkipError(TradingError):
    """최소 수량 미달 등으로 주문을 정상적으로 생략해야 할 때 (경고 수준)"""


class OrderNotConfirmedError(TradingError):
    """주문 발송 후 체결 여부를 확인하지 못했을 때 (상태 기록 금지, 다음 실행의 포지션 대조로 감지)"""
```

### (4) 의존성/주의
- 다른 프로젝트 모듈을 import 하지 않아야 합니다 (순환 import 방지).

### Step 검증
```powershell
python -m py_compile api/exceptions.py
```

---

## Step 2. `config.py` (Diff)

### (1) 파일 경로
`config.py`

### (2) 기존 코드 영역
- 2-A: L43-47 (`# 2단계 트레일링 매도 및 스케줄링 설정` 블록) 바로 뒤
- 2-B: L91-94 (`# 6. 파일 저장 경로 설정` 블록)
- 2-C: 파일 끝 (L95 이후)

### (3) 변경 코드

**2-A** — 앵커: `PRESERVATION_BUFFER_PCT = 0.002 # 최소 이익 보존선 슬리피지 허용 버퍼 (0.2%)`
```diff
 DAILY_EVAL_END_MIN = 15        # 일봉 마감 평가 종료 분 (09:15 KST)
 PRESERVATION_BUFFER_PCT = 0.002 # 최소 이익 보존선 슬리피지 허용 버퍼 (0.2%)
 
+# ------------------------------------------------------------------------------
+# 2-1. 실전 안전장치 설정 (주문 체결 확인 / 포지션 대조 / 경보)
+# ------------------------------------------------------------------------------
+TAKER_FEE_RATE = 0.0006                       # 시장가 수수료율 추정치 (PAPER 체결 및 체결확인 폴백 시 사용)
+ORDER_CONFIRM_RETRY_DELAYS = (0.5, 1.0, 2.0)  # fetch_order 체결 확인 재시도 대기 시간(초)
+PARTIAL_FILL_RATIO = 0.99                     # 체결 수량 < 요청 수량 × 0.99 이면 부분 체결로 간주
+RECONCILE_TOLERANCE_PCT = 0.01                # 포지션 대조 허용 오차 (state 수량의 1%, 최소 주문 수량과 비교해 큰 값 사용)
+RECONCILE_AUTO_SYNC = True                    # 허용 오차 이내 차이는 state를 거래소 값으로 자동 보정
+ALERT_COOLDOWN_SEC = 3600                     # 동일 경보 재발송 최소 간격(초)
+
 
 # ------------------------------------------------------------------------------
 # 3. 자금 관리 및 유닛 가중치 설정 (PRD 섹션 2)
```

**2-B** — 앵커: `LOG_FILE = LOG_DIR / "trading.log"`
```diff
 STATE_FILE = BASE_DIR / "state.json"
 EXCEL_FILE = BASE_DIR / "trade_history.xlsx"
 LOG_DIR = BASE_DIR / "logs"
 LOG_FILE = LOG_DIR / "trading.log"
+ALERT_THROTTLE_FILE = LOG_DIR / "alert_throttle.json"   # 경보 쿨다운 기록 (state.json과 분리)
```

**2-C** — 파일 맨 끝에 추가
```python


# ------------------------------------------------------------------------------
# 7. 실전 모드 설정 검증
# ------------------------------------------------------------------------------
def validate_live_config() -> list:
    """
    실전 모드(PAPER_TRADING=False)에서 필수 API 설정 누락 항목 이름 리스트를 반환합니다.
    빈 리스트면 정상입니다. 모의 모드에서는 항상 빈 리스트를 반환합니다.
    """
    if PAPER_TRADING:
        return []
    required = (
        ("BITGET_API_KEY", BITGET_API_KEY),
        ("BITGET_SECRET", BITGET_SECRET),
        ("BITGET_PASSPHRASE", BITGET_PASSPHRASE),
    )
    return [name for name, value in required if not str(value or "").strip()]
```

### (4) 의존성/주의
- 기존 상수 이름·값은 변경하지 않습니다 (`UNIT_DIVISOR` 변경은 Phase 2).
- `validate_live_config()`는 공백만 있는 키도 누락으로 판정합니다 (`strip()`).

### Step 검증
```powershell
python -m py_compile config.py
python -c "import config; print(config.TAKER_FEE_RATE, config.ALERT_THROTTLE_FILE.name, config.validate_live_config())"
```

---

## Step 3. `utils/mock_data.py` (신규)

### (1) 파일 경로
`utils/mock_data.py`

### (2) 기존 코드 영역
없음. (`main.py` L39-57 `generate_mock_ohlcv()`를 이 파일로 **이동**합니다. `main.py` 쪽 삭제는 Step 10 전체 교체로 처리)

### (3) 코드
```python
"""
utils/mock_data.py
모의 매매(PAPER_TRADING=True) 전용 샘플 OHLCV 생성기
- 실전 모드에서는 호출 즉시 예외를 발생시켜 가짜 데이터 매매를 원천 차단합니다.
"""

import math
from datetime import datetime, timezone, timedelta

import pandas as pd

from config import PAPER_TRADING

KST = timezone(timedelta(hours=9))


def generate_mock_ohlcv(limit: int = 100, timeframe: str = '1d') -> pd.DataFrame:
    """네트워크 연결 불가 시 모의 매매 검증을 위한 샘플 OHLCV 데이터를 생성합니다. (PAPER 전용)"""
    if not PAPER_TRADING:
        raise RuntimeError("[mock_data] 실전 모드(PAPER_TRADING=false)에서는 모의 OHLCV를 생성할 수 없습니다.")

    delta = timedelta(hours=4) if timeframe == '4h' else timedelta(days=1)
    dates = [datetime.now(KST) - (delta * i) for i in reversed(range(limit))]
    base_price = 30.0
    records = []
    for i, dt in enumerate(dates):
        price = base_price + math.sin(i / 5.0) * 8.0 + (i * 0.1)
        open_p = price - 0.2
        high_p = price + 1.2
        low_p = price - 1.2
        close_p = price
        records.append([
            int(dt.timestamp() * 1000),
            open_p, high_p, low_p, close_p, 50000.0, dt.strftime('%Y-%m-%d %H:%M:%S')
        ])
    return pd.DataFrame(records, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'datetime'])
```

### (4) 의존성/주의
- `utils/__init__.py`는 수정하지 않습니다 (현재 `format_progress_single_track`만 re-export).

### Step 검증
```powershell
python -m py_compile utils/mock_data.py
```

---

## Step 4. `utils/alert_throttle.py` (신규)

### (1) 파일 경로
`utils/alert_throttle.py`

### (2) 기존 코드 영역
없음 (신규)

### (3) 코드
```python
"""
utils/alert_throttle.py
동일 경보의 반복 발송을 막는 쿨다운 관리 모듈
- state.json 손상 시에도 동작하도록 logs/alert_throttle.json에 별도 저장합니다.
"""

import json
import time

from config import ALERT_THROTTLE_FILE, ALERT_COOLDOWN_SEC
from utils.logger import logger


def should_send_alert(key: str, cooldown_sec: int = ALERT_COOLDOWN_SEC) -> bool:
    """
    key 경보를 지금 보내도 되는지 판단합니다.
    마지막 발송 후 cooldown_sec가 지났으면 True를 반환하고 발송 시각을 기록합니다.
    """
    now = time.time()
    data = {}
    try:
        if ALERT_THROTTLE_FILE.exists():
            with open(ALERT_THROTTLE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
    except Exception:
        data = {}

    last_sent = float(data.get(key, 0.0) or 0.0)
    if now - last_sent < cooldown_sec:
        logger.info(f"[AlertThrottle] '{key}' 경보 쿨다운 중 (재발송 생략)")
        return False

    data[key] = now
    try:
        ALERT_THROTTLE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ALERT_THROTTLE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[AlertThrottle] 경보 쿨다운 기록 저장 실패: {e}")
    return True
```

### (4) 의존성/주의
- `logs/`는 `.gitignore` 대상이므로 추가 ignore 설정이 필요 없습니다.

### Step 검증
```powershell
python -m py_compile utils/alert_throttle.py
```

---

## Step 5. `api/bitget_client.py` (전체 교체)

### (1) 파일 경로
`api/bitget_client.py`

### (2) 기존 코드 영역
파일 전체 (L1-L247). 핵심 변경 지점:
- L111, L161, L211: `if self.paper_trading or not BITGET_API_KEY:` → API 키 누락 시 모의 동작하던 분기 **제거**
- L137-144: 실전 잔고 실패/0 → `MOCK_BALANCE` 반환 **제거**
- L189-191: 포지션 조회 실패 → 0 포지션 반환 **제거**
- L224-226: `params = {'posSide': pos_side}` → `{'hedged': True, ...}`
- 반환 타입: `create_hedge_order()` → `OrderResult`

### (3) 코드 (전체)
```python
"""
api/bitget_client.py
ccxt 기반 Bitget 선물 API 클라이언트 Wrapper
Hedge Mode 강제, 1X 레버리지, 교차마진 설정 및 주문/잔고/OHLCV 수집을 담당합니다.
- 실전 모드(PAPER_TRADING=False)에서는 어떠한 경우에도 모의 잔고/모의 포지션/모의 주문으로 대체하지 않습니다.
- 모든 주문은 체결 확인 후 OrderResult로 반환되며, 상태/손익 기록은 반드시 OrderResult 값을 기준으로 합니다.
"""

import json
import math
import time
from dataclasses import dataclass, field
from datetime import timezone, timedelta
from typing import Dict, Any, Optional, Tuple

import ccxt
import pandas as pd

from config import (
    BITGET_API_KEY,
    BITGET_SECRET,
    BITGET_PASSPHRASE,
    PAPER_TRADING,
    MOCK_BALANCE,
    SYMBOL,
    LEVERAGE,
    MARGIN_MODE,
    STATE_FILE,
    TAKER_FEE_RATE,
    ORDER_CONFIRM_RETRY_DELAYS,
)
from api.exceptions import (
    ExchangeConfigError,
    DataUnavailableError,
    OrderSkipError,
    OrderNotConfirmedError,
)
from utils.logger import logger

PAPER_AMOUNT_DECIMALS = 4   # 모의 매매 주문 수량 절사 자릿수


@dataclass
class OrderResult:
    """주문 체결 확인 결과 (상태/손익/엑셀 기록은 반드시 이 값 기준)"""
    order_id: str
    side: str
    pos_side: str
    is_close: bool
    requested: float        # 정밀도 보정 후 요청 수량
    filled: float           # 실제 체결 수량
    average: float          # 실제 체결 평균가
    fee: float              # 수수료 (USDT, 양수)
    is_paper: bool
    raw: Dict[str, Any] = field(default_factory=dict)


class BitgetClient:
    """
    Bitget 선물(Swap) API Wrapper
    """
    def __init__(self):
        self.paper_trading = PAPER_TRADING
        self.symbol = SYMBOL
        self.exchange = self._init_exchange()
        self._markets_loaded = False

    def _init_exchange(self) -> ccxt.bitget:
        """ccxt.bitget 거래소 인스턴스 초기화"""
        exchange_params = {
            'apiKey': BITGET_API_KEY,
            'secret': BITGET_SECRET,
            'password': BITGET_PASSPHRASE,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'swap',
            }
        }
        return ccxt.bitget(exchange_params)

    def _ensure_markets(self):
        """amount_to_precision / market() 사용 전 마켓 정보를 1회 로드합니다."""
        if not self._markets_loaded:
            self.exchange.load_markets()
            self._markets_loaded = True

    # ------------------------------------------------------------------
    # 거래소 환경 설정 및 검증
    # ------------------------------------------------------------------
    def setup_exchange(self):
        """
        거래소 레버리지(1X), 마진모드(Cross), 포지션모드(Hedge) 환경을 설정하고,
        실전 모드에서는 계정 설정이 기대값과 일치하는지 검증합니다. (불일치 시 ExchangeConfigError)
        """
        if self.paper_trading:
            logger.info("[BitgetClient] 모의 매매(PAPER_TRADING=True) 모드로 동작 중입니다. 거래소 설정 요청을 시뮬레이션합니다.")
            return

        self._ensure_markets()

        # 1. 헤지 모드 (Dual-side position) 설정
        try:
            self.exchange.set_position_mode(hedged=True, symbol=self.symbol)
            logger.info(f"[BitgetClient] {self.symbol} 포지션 모드: Hedge Mode (Dual-side) 설정 완료")
        except Exception as e:
            logger.warning(f"[BitgetClient] 포지션 모드 설정 응답 (이미 설정되었거나 포지션 보유 중일 수 있음): {e}")

        # 2. 마진 모드 (Cross) 설정
        try:
            self.exchange.set_margin_mode(MARGIN_MODE, self.symbol)
            logger.info(f"[BitgetClient] {self.symbol} 마진 모드: {MARGIN_MODE} 설정 완료")
        except Exception as e:
            logger.warning(f"[BitgetClient] 마진 모드 설정 응답: {e}")

        # 3. 레버리지 (1X) 설정
        try:
            self.exchange.set_leverage(LEVERAGE, self.symbol)
            logger.info(f"[BitgetClient] {self.symbol} 레버리지: {LEVERAGE}X 설정 완료")
        except Exception as e:
            logger.warning(f"[BitgetClient] 레버리지 설정 응답: {e}")

        # 4. 계정 설정 검증 (불일치 시 매매 중단)
        self._verify_account_mode()

    def _verify_account_mode(self):
        """
        Bitget v2 계정 정보(/api/v2/mix/account/account)로 포지션 모드·마진 모드·레버리지를 검증합니다.
        ccxt는 bitget fetchPositionMode를 지원하지 않으므로 암시적 API를 사용합니다.
        """
        market = self.exchange.market(self.symbol)

        contract_size = float(market.get('contractSize') or 1.0)
        if contract_size != 1.0:
            raise ExchangeConfigError(
                f"{self.symbol} contractSize={contract_size} (1이 아님). 수량 계산 로직 재검토가 필요하여 매매를 중단합니다."
            )

        try:
            resp = self.exchange.privateMixGetV2MixAccountAccount({
                'symbol': market['id'],
                'productType': 'USDT-FUTURES',
                'marginCoin': 'USDT',
            })
        except Exception as e:
            raise ExchangeConfigError(f"계정 설정 조회 실패: {e}") from e

        data = resp.get('data') or {}
        logger.info(f"[BitgetClient] 계정 설정 응답 키: {sorted(list(data.keys()))}")

        pos_mode = data.get('posMode')
        if pos_mode != 'hedge_mode':
            raise ExchangeConfigError(f"포지션 모드가 hedge_mode가 아닙니다 (현재: {pos_mode})")

        margin_mode = data.get('marginMode')
        if margin_mode and margin_mode != 'crossed':
            raise ExchangeConfigError(f"마진 모드가 crossed가 아닙니다 (현재: {margin_mode})")

        leverage = data.get('crossedMarginLeverage')
        if leverage not in (None, '') and float(leverage) != float(LEVERAGE):
            raise ExchangeConfigError(f"레버리지 불일치 (설정 {LEVERAGE}x / 계정 {leverage}x)")

        logger.info(f"[BitgetClient] 계정 설정 검증 통과: posMode={pos_mode}, marginMode={margin_mode}, leverage={leverage}")

    # ------------------------------------------------------------------
    # 시세 / 잔고 / 포지션 조회
    # ------------------------------------------------------------------
    def fetch_ohlcv(self, symbol: Optional[str] = None, timeframe: str = "1d", limit: int = 100) -> pd.DataFrame:
        """
        최근 OHLCV 데이터를 수집하여 DataFrame으로 반환합니다. (실패 시 예외 전파)
        """
        target_symbol = symbol or self.symbol
        try:
            ohlcv = self.exchange.fetch_ohlcv(target_symbol, timeframe=timeframe, limit=limit)
            if not ohlcv:
                logger.warning(f"[BitgetClient] OHLCV 데이터가 비어 있습니다: {target_symbol} ({timeframe})")
                return pd.DataFrame()

            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            kst_tz = timezone(timedelta(hours=9))
            df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True).dt.tz_convert(kst_tz).dt.strftime('%Y-%m-%d %H:%M:%S')
            logger.info(f"[BitgetClient] {target_symbol} {timeframe} 캔들 {len(df)}개 수집 완료 (마지막 캔들(KST): {df['datetime'].iloc[-1]})")
            return df
        except Exception as e:
            logger.error(f"[BitgetClient] OHLCV 수집 실패 ({target_symbol}, {timeframe}): {e}")
            raise

    def fetch_account_balance(self) -> Tuple[float, float]:
        """
        계좌의 (총 평가 잔고, 실시간 가용 잔고)를 반환합니다.
        - 실전: 조회 실패 또는 총 평가 잔고 0 이하 시 DataUnavailableError (모의 잔고 대체 금지)
        - 모의: MOCK_BALANCE 및 state.json 보유 포지션 기준 사용 증거금 차감액
        """
        if self.paper_trading:
            # 모의 모드: state.json 내 보유 수량/평단가를 바탕으로 가용 잔고 시뮬레이션
            used_margin = 0.0
            if STATE_FILE.exists():
                try:
                    with open(STATE_FILE, "r", encoding="utf-8") as f:
                        st = json.load(f)
                    for key in ("strategy_A", "strategy_B"):
                        s = st.get(key, {}) or {}
                        used_margin += float(s.get("total_qty", 0.0)) * float(s.get("avg_price", 0.0))
                except Exception:
                    used_margin = 0.0
            mock_free = max(0.0, MOCK_BALANCE - used_margin)
            return MOCK_BALANCE, mock_free

        try:
            balance = self.exchange.fetch_balance({'type': 'swap'})
        except Exception as e:
            raise DataUnavailableError(f"[BitgetClient] 실전 잔고 조회 실패: {e}") from e

        usdt_info = balance.get('USDT') or {}
        total_equity = float(usdt_info.get('total') or (balance.get('total') or {}).get('USDT') or 0.0)
        free_margin = float(usdt_info.get('free') or (balance.get('free') or {}).get('USDT') or 0.0)

        if total_equity <= 0:
            raise DataUnavailableError(
                f"[BitgetClient] 총 평가 잔고가 0 이하입니다 (total={total_equity}). 계정/API 권한을 확인하세요."
            )
        free_margin = max(0.0, free_margin)
        logger.info(f"[BitgetClient] 실시간 잔고 조회 완료: 총 평가 ${total_equity:,.2f} | 가용 ${free_margin:,.2f}")
        return total_equity, free_margin

    def fetch_total_balance(self) -> float:
        """계좌 총 평가 잔고를 반환합니다. (하위 호환용)"""
        total, _ = self.fetch_account_balance()
        return total

    def fetch_open_positions(self, symbol: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        현재 심볼의 Long 및 Short 포지션 정보를 튜플로 반환합니다: (long_pos, short_pos)
        - 실전: 조회 실패 시 DataUnavailableError (0 포지션으로 대체 금지)
        - 모의: 항상 0 포지션
        """
        target_symbol = symbol or self.symbol
        default_pos = {'contracts': 0.0, 'entryPrice': 0.0, 'unrealizedPnl': 0.0, 'percentage': 0.0}

        if self.paper_trading:
            return default_pos.copy(), default_pos.copy()

        try:
            positions = self.exchange.fetch_positions([target_symbol])
        except Exception as e:
            raise DataUnavailableError(f"[BitgetClient] 실전 포지션 조회 실패: {e}") from e

        long_pos = default_pos.copy()
        short_pos = default_pos.copy()
        for pos in positions or []:
            if pos.get('symbol') and pos.get('symbol') != target_symbol:
                continue
            contracts = float(pos.get('contracts') or 0.0)
            if contracts <= 0:
                continue
            pos_dict = {
                'contracts': contracts,
                'entryPrice': float(pos.get('entryPrice') or 0.0),
                'unrealizedPnl': float(pos.get('unrealizedPnl') or 0.0),
                'percentage': float(pos.get('percentage') or 0.0),
            }
            side = str(pos.get('side') or '').lower()
            if side == 'long':
                long_pos = pos_dict
            elif side == 'short':
                short_pos = pos_dict
        return long_pos, short_pos

    # ------------------------------------------------------------------
    # 주문 수량 보정
    # ------------------------------------------------------------------
    def get_min_amount(self, symbol: Optional[str] = None) -> float:
        """최소 주문 수량을 반환합니다. (모의: 0.0001)"""
        if self.paper_trading:
            return 10 ** (-PAPER_AMOUNT_DECIMALS)
        self._ensure_markets()
        market = self.exchange.market(symbol or self.symbol)
        amount_limits = (market.get('limits') or {}).get('amount') or {}
        return float(amount_limits.get('min') or 0.0)

    def normalize_amount(self, amount: float, symbol: Optional[str] = None) -> float:
        """
        주문 수량을 거래소 정밀도로 절사하고 최소 수량을 검증합니다.
        미달 시 OrderSkipError를 발생시킵니다.
        """
        target_symbol = symbol or self.symbol
        if amount is None or amount <= 0:
            raise OrderSkipError(f"주문 수량이 0 이하입니다 ({amount})")

        if self.paper_trading:
            factor = 10 ** PAPER_AMOUNT_DECIMALS
            normalized = math.floor(amount * factor) / factor
        else:
            self._ensure_markets()
            try:
                normalized = float(self.exchange.amount_to_precision(target_symbol, amount))
            except ccxt.InvalidOrder as e:
                raise OrderSkipError(f"정밀도 보정 실패 (수량 {amount}): {e}") from e

        min_amount = self.get_min_amount(target_symbol)
        if normalized <= 0 or normalized < min_amount:
            raise OrderSkipError(f"정밀도 보정 수량 {normalized} < 최소 주문 수량 {min_amount}")
        return normalized

    # ------------------------------------------------------------------
    # 주문 집행 및 체결 확인
    # ------------------------------------------------------------------
    def create_hedge_order(
        self,
        side: str,              # 'buy' or 'sell'
        amount: float,          # 주문 수량 (토큰 수)
        pos_side: str,          # 'long' or 'short'
        is_close: bool = False, # 청산 주문 여부
        symbol: Optional[str] = None,
        ref_price: float = 0.0  # 기준가 (모의 체결가 및 체결가 미확인 시 대체값)
    ) -> OrderResult:
        """
        Hedge Mode 시장가 주문 집행 후 체결을 확인하여 OrderResult를 반환합니다.
        - 롱 진입: side='buy',  pos_side='long',  is_close=False
        - 롱 청산: side='sell', pos_side='long',  is_close=True
        - 숏 진입: side='sell', pos_side='short', is_close=False
        - 숏 청산: side='buy',  pos_side='short', is_close=True
        ccxt(bitget)는 hedged=True + reduceOnly=True 조합에서 tradeSide='Close' 설정 및 side 반전을 자동 처리합니다.
        """
        target_symbol = symbol or self.symbol
        qty = self.normalize_amount(amount, target_symbol)
        action_label = f"[{'청산' if is_close else '진입'}] {pos_side.upper()} {side.upper()} {qty} Qty"

        if self.paper_trading:
            average = float(ref_price)
            fee = qty * average * TAKER_FEE_RATE
            logger.info(f"[BitgetClient] [PAPER TRADING] 주문 시뮬레이션: {action_label} @ ${average:,.4f} (추정 수수료 ${fee:,.4f})")
            return OrderResult(
                order_id='paper_mock_order_id', side=side, pos_side=pos_side, is_close=is_close,
                requested=qty, filled=qty, average=average, fee=fee, is_paper=True, raw={}
            )

        params: Dict[str, Any] = {
            'hedged': True,            # Bitget 헤지모드: tradeSide Open/Close 자동 설정
            'marginMode': MARGIN_MODE, # 'cross'
        }
        if is_close:
            params['reduceOnly'] = True

        pre_qty, pre_entry = self._position_snapshot(pos_side, target_symbol)

        try:
            logger.info(f"[BitgetClient] 실주문 발송: {action_label} (params={params})")
            order = self.exchange.create_order(
                symbol=target_symbol,
                type='market',
                side=side,
                amount=qty,
                params=params
            )
        except Exception as e:
            logger.error(f"[BitgetClient] 주문 실패 ({action_label}): {e}")
            raise

        order_id = str(order.get('id') or '')
        logger.info(f"[BitgetClient] 주문 접수: ID={order_id}, Status={order.get('status')}")
        return self._confirm_order(order_id, target_symbol, side, pos_side, is_close, qty, pre_qty, pre_entry, ref_price)

    def _position_snapshot(self, pos_side: str, symbol: str) -> Tuple[Optional[float], Optional[float]]:
        """체결 확인 폴백용 포지션 스냅샷 (실패 시 (None, None))"""
        try:
            long_pos, short_pos = self.fetch_open_positions(symbol)
        except Exception as e:
            logger.warning(f"[BitgetClient] 포지션 스냅샷 조회 실패 (체결 확인 폴백 불가): {e}")
            return None, None
        pos = long_pos if pos_side == 'long' else short_pos
        return float(pos.get('contracts', 0.0)), float(pos.get('entryPrice', 0.0))

    @staticmethod
    def _extract_fee(order: Dict[str, Any]) -> float:
        """ccxt 주문 객체에서 수수료 합계(양수)를 추출합니다."""
        fees = order.get('fees') or []
        if not fees and order.get('fee'):
            fees = [order['fee']]
        total = 0.0
        for fee in fees:
            try:
                total += abs(float((fee or {}).get('cost') or 0.0))
            except (TypeError, ValueError):
                continue
        return total

    def _confirm_order(
        self,
        order_id: str,
        symbol: str,
        side: str,
        pos_side: str,
        is_close: bool,
        requested: float,
        pre_qty: Optional[float],
        pre_entry: Optional[float],
        ref_price: float
    ) -> OrderResult:
        """
        1) fetch_order 폴링으로 체결 수량/평균가/수수료 확인
        2) 실패 시 포지션 수량 변화(pre→post)로 체결량 추정
        3) 모두 실패 시 OrderNotConfirmedError
        """
        last_error: Optional[Exception] = None
        if order_id:
            for delay in ORDER_CONFIRM_RETRY_DELAYS:
                time.sleep(delay)
                try:
                    fetched = self.exchange.fetch_order(order_id, symbol)
                except Exception as e:
                    last_error = e
                    logger.warning(f"[BitgetClient] 주문 조회 재시도 ({order_id}): {e}")
                    continue

                status = fetched.get('status')
                filled = float(fetched.get('filled') or 0.0)
                if status == 'open' or (status is None and filled <= 0):
                    continue  # 체결 대기
                if filled <= 0:
                    raise OrderNotConfirmedError(f"주문 {order_id} 체결 수량 0 (status={status})")

                average = float(fetched.get('average') or fetched.get('price') or 0.0) or float(ref_price)
                fee = self._extract_fee(fetched)
                logger.info(f"[BitgetClient] 체결 확인: ID={order_id}, 체결 {filled} @ ${average:,.4f}, 수수료 ${fee:,.4f}")
                return OrderResult(
                    order_id=order_id, side=side, pos_side=pos_side, is_close=is_close,
                    requested=requested, filled=filled, average=average, fee=fee, is_paper=False, raw=fetched
                )

        # 폴백: 포지션 수량 변화로 체결량 추정
        post_qty, post_entry = self._position_snapshot(pos_side, symbol)
        if pre_qty is not None and post_qty is not None:
            delta = abs(post_qty - pre_qty)
            if delta > 0:
                average = float(ref_price)
                if not is_close and post_qty > 0 and post_entry:
                    derived = (post_entry * post_qty - (pre_entry or 0.0) * pre_qty) / delta
                    if derived > 0:
                        average = derived
                fee = delta * average * TAKER_FEE_RATE
                logger.warning(
                    f"[BitgetClient] fetch_order 확인 실패 → 포지션 변화로 체결 추정: {delta} @ ${average:,.4f} (주문 {order_id})"
                )
                return OrderResult(
                    order_id=order_id, side=side, pos_side=pos_side, is_close=is_close,
                    requested=requested, filled=delta, average=average, fee=fee, is_paper=False,
                    raw={'fallback': 'position_delta', 'order_id': order_id}
                )

        raise OrderNotConfirmedError(f"주문 {order_id} 체결 확인 실패 (마지막 오류: {last_error})")


bitget_client = BitgetClient()
```

### (4) 의존성/주의
- **반환 타입 변경**: `create_hedge_order()`가 dict → `OrderResult`. 기존 호출부는 `manager/trader.py`뿐이며 Step 9에서 전체 교체합니다. Step 9 전에는 trader가 깨진 상태이므로 Step 5~9를 연속 적용하세요.
- `ref_price` 인자 신규 추가 (기본값 0.0) — 모의 체결가와 체결가 미확인 시 대체값으로 쓰입니다. 호출부에서 반드시 현재가를 넘깁니다.
- `privateMixGetV2MixAccountAccount` 존재는 설치본 ccxt 4.5.71에서 확인했습니다. 응답 필드(`posMode`, `marginMode`, `crossedMarginLeverage`)는 **첫 실전 `--reconcile` 실행 로그의 "계정 설정 응답 키"** 로 재확인하세요. 필드가 다르면 `ExchangeConfigError`로 **매매가 멈추는 방향으로 실패**합니다 (주문 0건, 안전).
- 시장가 주문의 `create_order()` 응답은 보통 `id`만 담기므로 `fetch_order()` 폴링이 필수입니다.

### Step 검증
```powershell
python -m py_compile api/bitget_client.py
```

---

## Step 6. `manager/cycle_manager.py` (Diff)

### (1) 파일 경로
`manager/cycle_manager.py`

### (2) 기존 코드 영역
- 6-A: L6-12 import 블록
- 6-B: L21-23 `__init__`
- 6-C: L82-90 `_load_state()`의 `except` 블록
- 6-D: L92-104 `_save_state()`
- 6-E: L291-296 `is_any_trailing_active()` 뒤 (클래스 끝, `cycle_manager = CycleManager()` 앞)에 메서드 추가

### (3) 변경 코드

**6-A** import
```diff
 import json
 import os
+import shutil
 from datetime import datetime, timezone, timedelta
 from pathlib import Path
-from typing import Dict, Any, Tuple
-from config import STATE_FILE, DUMMY_TARGET, WEIGHTED_UNIT_THRESHOLD, WEIGHT_NORMAL, WEIGHT_BOOST
+from typing import Dict, Any, Tuple, Optional
+from config import (
+    STATE_FILE, PAPER_TRADING,
+    DUMMY_TARGET, WEIGHTED_UNIT_THRESHOLD, WEIGHT_NORMAL, WEIGHT_BOOST
+)
 from utils.logger import logger
```

**6-B** `__init__` — 앵커: `self.state: Dict[str, Any] = self._load_state()`
```diff
     def __init__(self, state_file: Path = STATE_FILE):
         self.state_file = Path(state_file)
+        self.backup_file = self.state_file.with_suffix(".bak.json")   # 직전 정상본 보관 (state.bak.json)
+        self.load_error: Optional[str] = None   # 실전 모드 state 손상 사유 (설정 시 저장 차단 + 파이프라인 중단)
         self.state: Dict[str, Any] = self._load_state()
```

**6-C** `_load_state()` except 블록 — 앵커: `logger.error(f"[CycleManager] state.json 로드 실패, 기본 상태로 초기화합니다: {e}")`
```diff
         except Exception as e:
             logger.error(f"[CycleManager] state.json 로드 실패, 기본 상태로 초기화합니다: {e}")
-
+            backup_path = self._backup_corrupted_file()
+            if not PAPER_TRADING:
+                # 실전 모드: 기본값으로 덮어쓰지 않도록 저장을 차단하고 파이프라인에서 중단 처리
+                self.load_error = (
+                    f"state.json 로드 실패({e}). 손상 파일 백업: {backup_path}. "
+                    f"'python main.py --reconcile --adopt-exchange'로 복구하세요."
+                )
+                logger.critical(f"[CycleManager] {self.load_error}")
             default_state = {
                 "strategy_A": self._default_strategy_state(is_long=True),
                 "strategy_B": self._default_strategy_state(is_long=False),
                 "last_updated": self._get_kst_time_str()
             }
             return default_state
```

**6-D** `_save_state()` — 전체 메서드 교체
```diff
     def _save_state(self, state: Dict[str, Any]):
         """state.json을 임시 파일을 통해 원자적(Atomic)으로 저장합니다."""
+        if self.load_error:
+            logger.error("[CycleManager] state.json 손상 상태이므로 저장을 차단합니다 (기존 파일 보호).")
+            return
         state["last_updated"] = self._get_kst_time_str()
         temp_file = self.state_file.with_suffix(".tmp")
         try:
             with open(temp_file, "w", encoding="utf-8") as f:
                 json.dump(state, f, indent=2, ensure_ascii=False)
+            if self.state_file.exists():
+                shutil.copy2(self.state_file, self.backup_file)   # 직전 정상본 1부 보관
             temp_file.replace(self.state_file)
             self.state = state
         except Exception as e:
             logger.error(f"[CycleManager] state.json 저장 실패: {e}")
             if temp_file.exists():
                 temp_file.unlink()
```

**6-E** 메서드 추가 — 앵커: `return bool(a_active or b_active)` 다음 줄(클래스 내부, 들여쓰기 4칸)
```python

    # ------------------------------------------------------------------
    # 손상 대응 / 포지션 대조 지원 메서드
    # ------------------------------------------------------------------
    def _build_default_state(self) -> Dict[str, Any]:
        """A/B 전략 기본 상태 딕셔너리를 생성합니다."""
        return {
            "strategy_A": self._default_strategy_state(is_long=True),
            "strategy_B": self._default_strategy_state(is_long=False),
            "last_updated": self._get_kst_time_str()
        }

    def _backup_corrupted_file(self) -> Optional[Path]:
        """손상된 state.json을 타임스탬프 사본(state.corrupt_YYYYMMDD_HHMMSS.json)으로 보관합니다."""
        try:
            stamp = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
            backup_path = self.state_file.with_name(f"{self.state_file.stem}.corrupt_{stamp}.json")
            shutil.copy2(self.state_file, backup_path)
            logger.warning(f"[CycleManager] 손상된 state 파일 백업 완료: {backup_path.name}")
            return backup_path
        except Exception as e:
            logger.error(f"[CycleManager] 손상 state 파일 백업 실패: {e}")
            return None

    def reload(self):
        """디스크의 state.json을 다시 읽어 메모리 상태를 갱신합니다. (--loop 모드 틱마다 호출)"""
        self.load_error = None
        self.state = self._load_state()

    def recover_with_default_state(self):
        """손상 상태에서 기본 상태로 재생성합니다. (--reconcile --adopt-exchange 수동 복구 전용)"""
        self.load_error = None
        self._save_state(self._build_default_state())
        logger.warning("[CycleManager] 손상된 state.json을 기본 상태로 재생성했습니다 (거래소 포지션 채택 예정).")

    def sync_position_values(self, strategy_name: str, qty: float, avg_price: float):
        """포지션 대조 결과(허용 오차 이내)를 반영해 수량/평단을 거래소 값으로 보정합니다."""
        s = self.state[strategy_name]
        old_qty, old_avg = s.get("total_qty", 0.0), s.get("avg_price", 0.0)
        s["total_qty"] = float(qty)
        if avg_price > 0:
            s["avg_price"] = float(avg_price)
        self._save_state(self.state)
        logger.info(
            f"[CycleManager] [{strategy_name}] 거래소 기준 자동 보정: "
            f"수량 {old_qty:,.4f} → {s['total_qty']:,.4f}, 평단 ${old_avg:,.4f} → ${s['avg_price']:,.4f}"
        )

    def adopt_exchange_position(self, strategy_name: str, qty: float, avg_price: float, date_str: str):
        """거래소 포지션을 state에 강제 채택합니다. (수동 복구 전용, 더미 완료·최소 1회차로 간주)"""
        s = self.state[strategy_name]
        s["total_qty"] = float(qty)
        s["avg_price"] = float(avg_price)
        s["dummy_count"] = DUMMY_TARGET
        s["executed_units"] = max(int(s.get("executed_units", 0)), 1)
        s["last_action"] = "SYNC_ADOPT"
        s["last_action_date"] = date_str
        self._save_state(self.state)
        logger.warning(
            f"[CycleManager] [{strategy_name}] 거래소 포지션 채택: {qty:,.4f} Qty @ ${avg_price:,.4f} "
            f"(executed_units={s['executed_units']})"
        )

    def reduce_position(self, strategy_name: str, qty: float, date_str: str):
        """부분 청산 체결 수량만큼 보유 수량을 차감합니다. (평단 유지)"""
        s = self.state[strategy_name]
        s["total_qty"] = max(0.0, float(s.get("total_qty", 0.0)) - float(qty))
        s["last_action"] = "SELL_PARTIAL"
        s["last_action_date"] = date_str
        self._save_state(self.state)
        logger.warning(f"[CycleManager] [{strategy_name}] 부분 청산 반영: 잔여 수량 {s['total_qty']:,.4f}")
```

### (4) 의존성/주의
- `self.load_error`는 반드시 `self._load_state()` 호출 **전에** 초기화해야 합니다 (6-B 순서 유지). `_load_state()` 내부의 신규 파일 생성 분기가 `_save_state()`를 호출하기 때문입니다.
- 모듈 하단 `cycle_manager = CycleManager()`는 import 시 실행되므로, 손상 시 **예외를 던지지 않고** `load_error`만 설정합니다 (main에서 경보 후 중단).
- `os` import는 기존 그대로 둡니다 (미사용이지만 본 Phase 범위 밖).

### Step 검증
```powershell
python -m py_compile manager/cycle_manager.py
```

---

## Step 7. `utils/excel_logger.py` (Diff)

### (1) 파일 경로
`utils/excel_logger.py`

### (2) 기존 코드 영역
L174-179 `append_record()` 내부 `event_fills` 딕셔너리

### (3) 변경 코드
```diff
             event_fills = {
                 "BUY_UNIT": PatternFill(start_color="E2EFDA", end_color="E2EFDA", fill_type="solid"),   # 연초록
                 "SELL_CLEAR": PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid"), # 연주황
                 "BUY_DUMMY": PatternFill(start_color="EDEDED", end_color="EDEDED", fill_type="solid"),  # 연회색
                 "RESET_DUMMY": PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid"), # 연노랑 (리셋)
+                "SELL_CLEAR_PARTIAL": PatternFill(start_color="F8CBAD", end_color="F8CBAD", fill_type="solid"), # 진주황 (부분 청산)
+                "SYNC_RESET": PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid"),  # 연파랑 (대조 복구)
+                "SYNC_ADOPT": PatternFill(start_color="DDEBF7", end_color="DDEBF7", fill_type="solid"),  # 연파랑 (대조 복구)
             }
```

### (4) 의존성/주의
- `SELL_CLEAR_PARTIAL`은 이름에 `SELL_CLEAR`가 포함되어 있어 `update_pnl_chart()`의 실현손익 집계(`"SELL_CLEAR" not in event_type` 필터)에 **자동 포함**됩니다 (의도된 동작).
- `SYNC_*` 이벤트는 차트 집계에서 제외됩니다.

### Step 검증
```powershell
python -m py_compile utils/excel_logger.py
```

---

## Step 8. `manager/reconciler.py` (신규)

### (1) 파일 경로
`manager/reconciler.py`

### (2) 기존 코드 영역
없음 (신규)

### (3) 코드
```python
"""
manager/reconciler.py
state.json ↔ 거래소 실제 포지션 대조(Reconciliation) 모듈
- 실전 모드에서만 거래소를 조회하며, 불일치(MISMATCH) 전략은 매매를 차단합니다.
- 허용 오차 이내 차이는 RECONCILE_AUTO_SYNC=True일 때 거래소 값으로 자동 보정합니다.
"""

from dataclasses import dataclass
from typing import Dict

from config import RECONCILE_TOLERANCE_PCT, RECONCILE_AUTO_SYNC
from api.bitget_client import BitgetClient, bitget_client
from manager.cycle_manager import CycleManager, cycle_manager
from utils.excel_logger import ExcelLogger, excel_logger
from utils.logger import logger

STRATEGY_POS_SIDE = {"strategy_A": "long", "strategy_B": "short"}


@dataclass
class ReconcileResult:
    strategy: str
    pos_side: str
    state_qty: float
    exchange_qty: float
    state_avg: float
    exchange_avg: float
    status: str      # OK / SYNCED / MISMATCH / SKIPPED_PAPER
    detail: str

    @property
    def blocked(self) -> bool:
        """매매 차단 대상 여부"""
        return self.status == "MISMATCH"


class PositionReconciler:
    """state.json과 거래소 포지션을 비교·보정하는 대조기"""

    def __init__(
        self,
        client: BitgetClient = bitget_client,
        cycles: CycleManager = cycle_manager,
        excel: ExcelLogger = excel_logger
    ):
        self.client = client
        self.cycles = cycles
        self.excel = excel

    def _fetch_exchange_positions(self) -> Dict[str, Dict[str, float]]:
        long_pos, short_pos = self.client.fetch_open_positions()  # 실전 실패 시 DataUnavailableError
        return {"strategy_A": long_pos, "strategy_B": short_pos}

    def reconcile(self, auto_sync: bool = RECONCILE_AUTO_SYNC) -> Dict[str, ReconcileResult]:
        """전략별 대조 결과를 반환합니다. (모의 모드는 SKIPPED_PAPER)"""
        results: Dict[str, ReconcileResult] = {}

        if self.client.paper_trading:
            for name, side in STRATEGY_POS_SIDE.items():
                st = self.cycles.get_strategy_state(name)
                qty = float(st.get("total_qty", 0.0))
                avg = float(st.get("avg_price", 0.0))
                results[name] = ReconcileResult(name, side, qty, qty, avg, avg, "SKIPPED_PAPER", "모의 매매 모드 - 대조 생략")
            return results

        positions = self._fetch_exchange_positions()
        min_amount = self.client.get_min_amount()

        for name, side in STRATEGY_POS_SIDE.items():
            st = self.cycles.get_strategy_state(name)
            s_qty = float(st.get("total_qty", 0.0))
            s_avg = float(st.get("avg_price", 0.0))
            pos = positions[name]
            e_qty = float(pos.get("contracts", 0.0))
            e_avg = float(pos.get("entryPrice", 0.0))
            tol = max(min_amount, s_qty * RECONCILE_TOLERANCE_PCT)
            diff = abs(s_qty - e_qty)

            if s_qty <= 0 and e_qty <= 0:
                status, detail = "OK", "양쪽 모두 미보유"
            elif s_qty > 0 and e_qty <= 0:
                status, detail = "MISMATCH", f"state {s_qty:,.4f} 보유 / 거래소 0 (수동 청산·강제 청산 의심)"
            elif s_qty <= 0 and e_qty > 0:
                status, detail = "MISMATCH", f"state 0 / 거래소 {e_qty:,.4f} 보유 (수동 진입·미기록 체결 의심)"
            elif diff <= tol:
                avg_diff = e_avg > 0 and abs(s_avg - e_avg) > 1e-9
                if auto_sync and (diff > 0 or avg_diff):
                    self.cycles.sync_position_values(name, e_qty, e_avg if e_avg > 0 else s_avg)
                    status, detail = "SYNCED", f"허용 오차 이내 자동 보정 (수량 차 {diff:,.6f}, 평단 ${s_avg:,.4f}→${e_avg:,.4f})"
                else:
                    status, detail = "OK", f"일치 (수량 차 {diff:,.6f} ≤ 허용 {tol:,.6f})"
            else:
                status, detail = "MISMATCH", f"수량 차이 {diff:,.4f} > 허용 {tol:,.4f} (state {s_qty:,.4f} / 거래소 {e_qty:,.4f})"

            log_fn = logger.warning if status == "MISMATCH" else logger.info
            log_fn(f"[Reconciler] [{name}] {status}: {detail}")
            results[name] = ReconcileResult(name, side, s_qty, e_qty, s_avg, e_avg, status, detail)

        return results

    def adopt_exchange(self, date_str: str) -> Dict[str, ReconcileResult]:
        """거래소 포지션 값을 state에 강제 채택합니다. (python main.py --reconcile --adopt-exchange 전용)"""
        if self.cycles.load_error:
            self.cycles.recover_with_default_state()

        positions = self._fetch_exchange_positions()
        results: Dict[str, ReconcileResult] = {}

        for name, side in STRATEGY_POS_SIDE.items():
            st = self.cycles.get_strategy_state(name)
            s_qty = float(st.get("total_qty", 0.0))
            s_avg = float(st.get("avg_price", 0.0))
            pos = positions[name]
            e_qty = float(pos.get("contracts", 0.0))
            e_avg = float(pos.get("entryPrice", 0.0))

            if e_qty <= 0 and s_qty > 0:
                self.cycles.reset_cycle(name, date_str=date_str, transfer_to_dummy1=False)
                self.excel.append_record(
                    date_str=date_str, strategy_name=name, event_type="SYNC_RESET",
                    price=None, qty=None, unit_label="SYNC", dummy_status="0 / 2", total_balance=0.0,
                    note=f"거래소 포지션 0 채택: state {s_qty:,.4f} Qty → 싸이클 초기화 (수동 복구)",
                    auto_update_chart=False
                )
                status, detail = "SYNCED", "거래소 0 채택 → 싸이클 초기화"
            elif e_qty > 0:
                self.cycles.adopt_exchange_position(name, e_qty, e_avg, date_str)
                self.excel.append_record(
                    date_str=date_str, strategy_name=name, event_type="SYNC_ADOPT",
                    price=e_avg, qty=e_qty, unit_label="SYNC", dummy_status="2 / 2", total_balance=0.0,
                    note=f"거래소 포지션 채택: {e_qty:,.4f} Qty @ ${e_avg:,.2f} (기존 state {s_qty:,.4f} @ ${s_avg:,.2f})",
                    auto_update_chart=False
                )
                status, detail = "SYNCED", f"거래소 {e_qty:,.4f} @ ${e_avg:,.2f} 채택"
            else:
                status, detail = "OK", "양쪽 모두 미보유"

            logger.warning(f"[Reconciler] [{name}] 수동 채택 결과: {status} - {detail}")
            results[name] = ReconcileResult(name, side, s_qty, e_qty, s_avg, e_avg, status, detail)

        return results


reconciler = PositionReconciler()
```

### (4) 의존성/주의
- `cycles.sync_position_values`, `adopt_exchange_position`, `recover_with_default_state`, `load_error` (Step 6), `client.get_min_amount` (Step 5)에 의존합니다.
- 모의 모드에서는 거래소를 호출하지 않습니다.

### Step 검증
```powershell
python -m py_compile manager/reconciler.py
```

---

## Step 9. `manager/trader.py` (전체 교체)

### (1) 파일 경로
`manager/trader.py`

### (2) 기존 코드 영역
파일 전체 (L1-L631). 기존 A/B 중복 블록(L85-191 청산, L198-494 진입, L516-624 2단계)을 공통 메서드로 통합합니다. 기존 텔레그램/엑셀/로그 문구는 최대한 유지합니다.

핵심 변경:
- 청산: `record_cycle_realized_pnl()`을 **주문 전(L111, L165, L533, L588) → 체결 확인 후**로 이동
- 진입: 정밀도 보정 전 `target_qty`/`current_price` 기록(L293-298, L442-447) → `OrderResult.filled/average` 기록
- 진입 평가 시 L75-76의 오래된 state 사본 대신 **최신 state 재조회** (트레일링 돌입 당일 진입 차단)
- 당일 청산 후 더미 이관: `reset_cycle(transfer_to_dummy1=True)`(L217, L366, `cycle_id` 이중 증가) → `consume_dummy()`
- 전략별 `try/except` 격리, `blocked` 전략 스킵

### (3) 코드 (전체)
```python
"""
manager/trader.py
Bitget Hedge Mode 주문(Long/Short 분리 진입/청산) 집행 및 매도 우선 원칙 제어 모듈 (PRD 섹션 2, 4, 6)
- 모든 상태/손익/엑셀 기록은 체결 확인된 OrderResult(실체결가·수량·수수료) 기준으로 수행합니다.
- 전략(A/B)별 처리는 서로 격리되어 한 전략의 오류가 다른 전략 평가를 중단시키지 않습니다.
"""

from typing import Dict, Any, List, Optional, Set, Tuple
from datetime import datetime, timezone, timedelta
import pandas as pd
from config import UNIT_DIVISOR, PRESERVATION_BUFFER_PCT, MIN_ORDER_USD, PARTIAL_FILL_RATIO, ALERT_COOLDOWN_SEC
from api.bitget_client import BitgetClient, bitget_client
from api.exceptions import OrderSkipError
from manager.cycle_manager import CycleManager, cycle_manager
from strategy.indicator import IndicatorCalculator
from strategy.strategy_a_long import StrategyALong
from strategy.strategy_b_short import StrategyBShort
from utils.alert_throttle import should_send_alert
from utils.excel_logger import ExcelLogger, excel_logger
from utils.logger import logger
from utils.notifier import TelegramNotifier, notifier
from utils.formatters import format_progress_single_track

KST = timezone(timedelta(hours=9))

STRATEGY_NAMES = ("strategy_A", "strategy_B")

# 전략별 방향/주문/표시 설정 (A=롱, B=숏)
SIDE_CFG: Dict[str, Dict[str, Any]] = {
    "strategy_A": {
        "label": "롱", "opp_label": "숏", "opposite": "strategy_B",
        "pos_side": "long", "open_side": "buy", "close_side": "sell", "direction": 1,
        "emoji": "🟢", "entry_title": "롱 실전 매수", "entry_summary": "매수 체결",
    },
    "strategy_B": {
        "label": "숏", "opp_label": "롱", "opposite": "strategy_A",
        "pos_side": "short", "open_side": "sell", "close_side": "buy", "direction": -1,
        "emoji": "🔴", "entry_title": "숏 실전 진입", "entry_summary": "숏 진입 체결",
    },
}


class Trader:
    """
    일봉 마감 시점 A/B 전략 조건 평가 및 주문 집행 통합 총괄 매니저
    """
    def __init__(
        self,
        client: BitgetClient = bitget_client,
        cycles: CycleManager = cycle_manager,
        excel: ExcelLogger = excel_logger,
        telegram: TelegramNotifier = notifier
    ):
        self.client = client
        self.cycles = cycles
        self.excel = excel
        self.notifier = telegram
        self.strategy_a = StrategyALong()
        self.strategy_b = StrategyBShort()
        self.strategies = {"strategy_A": self.strategy_a, "strategy_B": self.strategy_b}
        self.last_total_balance: float = 0.0
        self.last_free_margin: float = 0.0
        self.last_unit_base_usd: float = 0.0
        self.last_eval: Dict[str, Dict[str, Any]] = {name: {} for name in STRATEGY_NAMES}

    # ------------------------------------------------------------------
    # 하위 호환 속성 (main.py 일일 보고서용)
    # ------------------------------------------------------------------
    @property
    def last_eval_a(self) -> Dict[str, Any]:
        return self.last_eval.get("strategy_A", {})

    @property
    def last_eval_b(self) -> Dict[str, Any]:
        return self.last_eval.get("strategy_B", {})

    # ------------------------------------------------------------------
    # 공통 유틸
    # ------------------------------------------------------------------
    @staticmethod
    def _escape(text: str) -> str:
        """텔레그램 HTML 파싱 오류 방지를 위한 특수문자 이스케이프"""
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _refresh_balance(self) -> Tuple[float, float]:
        """총 평가 잔고/가용 잔고를 조회해 캐시합니다. (실전 실패 시 예외 전파)"""
        total_balance, free_margin = self.client.fetch_account_balance()
        self.last_total_balance = total_balance
        self.last_free_margin = free_margin
        self.last_unit_base_usd = total_balance / UNIT_DIVISOR
        return total_balance, free_margin

    def _set_eval(self, strategy_name: str, signal_met: bool, status: str, reason: str, summary: str):
        self.last_eval[strategy_name] = {
            "signal_met": signal_met,
            "status": status,
            "reason": reason,
            "summary": summary,
        }

    def _resolve_close_qty(self, strategy_name: str, state_qty: float) -> float:
        """청산 수량: 실전은 거래소 실제 포지션 수량, 모의는 state 수량"""
        if self.client.paper_trading:
            return state_qty
        long_pos, short_pos = self.client.fetch_open_positions()
        pos = long_pos if SIDE_CFG[strategy_name]["pos_side"] == "long" else short_pos
        exchange_qty = float(pos.get("contracts", 0.0))
        if abs(exchange_qty - state_qty) > 1e-9:
            logger.info(
                f"[Trader] [{strategy_name}] 청산 수량은 거래소 실제 포지션 기준 사용: "
                f"state {state_qty:,.4f} → 거래소 {exchange_qty:,.4f}"
            )
        return exchange_qty

    # ------------------------------------------------------------------
    # 일봉 마감 파이프라인
    # ------------------------------------------------------------------
    def process_daily_cycle(self, indicators: Dict[str, Any], blocked: Optional[Set[str]] = None) -> List[str]:
        """
        일봉 마감 시점 파이프라인 전체를 처리합니다.
        1. 계좌 총 평가 잔고 및 가용 잔고 확인
        2. 전고점/전저점 갱신
        3. [매도 우선] 전략 A 롱 / 전략 B 숏 1차 청산 평가 (전략별 오류 격리)
        4. [매수 진입] 전략 A 롱 / 전략 B 숏 진입 평가 (당일 청산 시 1차 더미로 이관)
        blocked: 포지션 대조 불일치(MISMATCH)로 매매를 차단할 전략명 집합
        """
        blocked = set(blocked or set())
        date_str = indicators.get('candle_date', '')
        current_price = indicators['current_price']
        execution_logs: List[str] = []
        self.last_eval = {name: {} for name in STRATEGY_NAMES}

        # 1. 총 평가 잔고, 실시간 가용 잔고 및 1 Unit 기준금액 산출
        total_balance, free_margin = self._refresh_balance()
        logger.info(
            f"[Trader] 계좌 총 평가 잔고: ${total_balance:,.2f} | 가용 잔고: ${free_margin:,.2f} | "
            f"1.0 Unit 기준금액: 단일 ${self.last_unit_base_usd:,.2f} / 헤지 ${free_margin / UNIT_DIVISOR:,.2f}"
        )

        # 2. 전고점/전저점 갱신 (차단 전략 제외)
        for name in STRATEGY_NAMES:
            if name not in blocked:
                self.cycles.update_price_extremes(name, current_price)

        cleared_today = {name: False for name in STRATEGY_NAMES}
        failed: Set[str] = set()

        # 3. [매도 우선 원칙] 1차 청산 평가
        for name in STRATEGY_NAMES:
            label = SIDE_CFG[name]["label"]
            if name in blocked:
                execution_logs.append(f"⛔ [{label} 매매 차단] 거래소 포지션 불일치로 청산/진입 평가를 생략했습니다.")
                self._set_eval(
                    name, False, "BLOCKED_MISMATCH", "거래소 포지션 불일치",
                    "⛔ 포지션 불일치로 매매 차단 (python main.py --reconcile 확인 필요)"
                )
                continue
            try:
                cleared_today[name] = self._evaluate_stage1_exit(name, indicators, date_str, execution_logs)
            except OrderSkipError as e:
                logger.warning(f"[Trader] [{name}] {label} 청산 주문 스킵: {e}")
                execution_logs.append(f"⚠️ [{label} 청산 주문 스킵] {e}")
            except Exception as e:
                logger.error(f"[Trader] [{name}] {label} 청산 처리 실패: {e}", exc_info=True)
                execution_logs.append(f"❌ [{label} 청산 처리 실패] {e}")
                failed.add(name)
                self._set_eval(name, False, "EXIT_FAILED", str(e), f"❌ 청산 처리 실패로 진입 평가 생략 ({e})")

        # 4. [매수 진입 단계] 진입 평가
        for name in STRATEGY_NAMES:
            if name in blocked or name in failed:
                continue
            label = SIDE_CFG[name]["label"]
            try:
                self._evaluate_entry(name, indicators, date_str, cleared_today[name], execution_logs)
            except OrderSkipError as e:
                logger.warning(f"[Trader] [{name}] {label} 진입 주문 스킵: {e}")
                execution_logs.append(f"⚠️ [{label} 진입 주문 스킵] {e}")
                self._set_eval(name, True, "SKIPPED_ORDER", str(e), f"⚠️ 주문 스킵 ({e})")
            except Exception as e:
                logger.error(f"[Trader] [{name}] {label} 진입 처리 실패: {e}", exc_info=True)
                execution_logs.append(f"❌ [{label} 진입 처리 실패] {e}")
                self._set_eval(name, True, "ENTRY_FAILED", str(e), f"❌ 진입 처리 실패 ({e})")

        return execution_logs

    def _evaluate_stage1_exit(
        self,
        strategy_name: str,
        indicators: Dict[str, Any],
        date_str: str,
        execution_logs: List[str]
    ) -> bool:
        """1차 청산 평가. 즉시 전량 청산이 완료되면 True(당일 청산)를 반환합니다."""
        cfg = SIDE_CFG[strategy_name]
        label = cfg["label"]
        strategy = self.strategies[strategy_name]
        current_price = indicators['current_price']
        state = self.cycles.get_strategy_state(strategy_name)

        exit_met, action, exit_reason, target_price = strategy.check_stage1_exit_signal(
            current_price=current_price,
            indicators=indicators,
            cycle_info=state
        )

        if not exit_met:
            logger.info(f"[Trader] [{strategy_name}] {label} 1차 청산 조건 미충족 ({exit_reason})")
            return False

        if action == "ENTER_STAGE2":
            # 상승장/과낙폭(과반등) 익절: 즉시 청산하지 않고 2단계 4H 5MA 감시 모드 활성화
            self.cycles.set_trailing_mode(
                strategy_name=strategy_name,
                target_price=target_price,
                base_price=target_price,
                reason=exit_reason,
                date_str=date_str
            )
            execution_logs.append(f"🟡 [{label} 2단계 트레일링 돌입] {exit_reason} (최소보존선: ${target_price:,.2f})")
            return False

        if action == "IMMEDIATE_EXIT":
            logger.info(f"[Trader] [{strategy_name}] {label} 즉시 전량 청산 충족! 사유: {exit_reason}")
            closed, log_msg = self._close_position(
                strategy_name=strategy_name,
                ref_price=current_price,
                date_str=date_str,
                reason=exit_reason,
                unit_label="ALL_CLEAR",
                log_title=f"{label} 즉시 전량 청산",
                target_price=target_price
            )
            execution_logs.append(log_msg)
            return closed

        return False

    def _close_position(
        self,
        strategy_name: str,
        ref_price: float,
        date_str: str,
        reason: str,
        unit_label: str,
        log_title: str,
        note_prefix: str = "",
        target_price: Optional[float] = None
    ) -> Tuple[bool, str]:
        """
        보유 포지션 전량 청산: 주문 → 체결 확인 → 실현손익 기록 → 엑셀 기록 → 싸이클 초기화 순서로 처리합니다.
        Returns: (전량 청산 완료 여부, 실행 로그 메시지)
        """
        cfg = SIDE_CFG[strategy_name]
        label = cfg["label"]
        state = self.cycles.get_strategy_state(strategy_name)
        avg_price = float(state.get('avg_price', 0.0))
        state_qty = float(state.get('total_qty', 0.0))
        close_qty = self._resolve_close_qty(strategy_name, state_qty)
        target_txt = f"목표: ${target_price:,.2f}, " if target_price else ""

        if close_qty <= 0:
            self.excel.append_record(
                date_str=date_str,
                strategy_name=strategy_name,
                event_type="SELL_CLEAR",
                price=None,
                qty=None,
                unit_label=unit_label,
                dummy_status="0 / 2",
                total_balance=self.last_total_balance,
                note=f"{note_prefix}{reason} (보유 수량 0 - 주문 없이 싸이클 초기화)",
                realized_pnl=0.0,
                pnl_pct=0.0
            )
            self.cycles.reset_cycle(strategy_name, date_str=date_str, transfer_to_dummy1=False)
            return True, f"⚪ [{log_title}] 보유 수량 0 → 주문 없이 싸이클 초기화 ({reason})"

        # 1) 주문 → 체결 확인 (실패 시 예외 전파: 상태/손익 기록 없음)
        result = self.client.create_hedge_order(
            side=cfg["close_side"],
            amount=close_qty,
            pos_side=cfg["pos_side"],
            is_close=True,
            ref_price=ref_price
        )
        fill_price = result.average if result.average > 0 else ref_price
        filled = result.filled
        direction = cfg["direction"]

        # 2) 실현손익 (체결가 기준, 청산 수수료 차감)
        if avg_price > 0:
            pnl_usd = (fill_price - avg_price) * filled * direction - result.fee
            pnl_pct = ((fill_price / avg_price) - 1.0) * 100.0 * direction
        else:
            pnl_usd = -result.fee
            pnl_pct = 0.0
        self.cycles.record_cycle_realized_pnl(strategy_name, pnl_usd, pnl_pct)
        note = (
            f"{note_prefix}{reason} ({target_txt}손익: {pnl_usd:+,.2f} USD, {pnl_pct:+.2f}%, "
            f"수수료: ${result.fee:,.4f})"
        )

        # 3) 부분 체결: 잔량 유지 (싸이클 초기화 보류)
        if filled < close_qty * PARTIAL_FILL_RATIO:
            self.cycles.reduce_position(strategy_name, filled, date_str)
            self.excel.append_record(
                date_str=date_str,
                strategy_name=strategy_name,
                event_type="SELL_CLEAR_PARTIAL",
                price=fill_price,
                qty=filled,
                unit_label=f"{unit_label}_PARTIAL",
                dummy_status="-",
                total_balance=self.last_total_balance,
                note=f"[부분 체결 {filled:,.4f}/{close_qty:,.4f}] {note}",
                realized_pnl=pnl_usd,
                pnl_pct=pnl_pct
            )
            msg = (
                f"⚠️ [{label} 부분 청산] 요청 {close_qty:,.4f} / 체결 {filled:,.4f} Qty "
                f"(체결가 ${fill_price:,.2f}, 손익 {pnl_usd:+,.2f} USD) - 잔량 유지, 다음 실행에서 재평가"
            )
            self.notifier.send_message(f"<b>⚠️ [Bitget V3 Switch 부분 청산 경보]</b>\n• {self._escape(msg)}")
            return False, msg

        # 4) 전량 청산: 엑셀 기록 → 싸이클 초기화
        self.excel.append_record(
            date_str=date_str,
            strategy_name=strategy_name,
            event_type="SELL_CLEAR",
            price=fill_price,
            qty=filled,
            unit_label=unit_label,
            dummy_status="0 / 2",
            total_balance=self.last_total_balance,
            note=note,
            realized_pnl=pnl_usd,
            pnl_pct=pnl_pct
        )
        self.cycles.reset_cycle(strategy_name, date_str=date_str, transfer_to_dummy1=False)
        msg = (
            f"{cfg['emoji']} [{log_title}] {reason} (체결가: ${fill_price:,.2f}, 수량: {filled:,.4f}, "
            f"확정손익: {pnl_usd:+,.2f} USD, {pnl_pct:+.2f}%)"
        )
        return True, msg

    def _evaluate_entry(
        self,
        strategy_name: str,
        indicators: Dict[str, Any],
        date_str: str,
        cleared_today: bool,
        execution_logs: List[str]
    ):
        """진입 신호 평가 및 더미 소진/실전 매수 집행"""
        cfg = SIDE_CFG[strategy_name]
        label = cfg["label"]
        strategy = self.strategies[strategy_name]

        # 최신 state 재조회 (당일 트레일링 돌입 여부 반영)
        state = self.cycles.get_strategy_state(strategy_name)
        if state.get("trailing_mode", False):
            reason = "2단계 트레일링 감시 진행 중 (신규 진입 스킵)"
            logger.info(f"[Trader] [{strategy_name}] {reason}")
            self._set_eval(strategy_name, False, "TRAILING_ACTIVE", reason, "🟡 2단계 트레일링 감시 진행 중 (신규 진입 스킵)")
            return

        entry_met, entry_reason = strategy.check_entry_signal(
            current_candle=indicators,
            prev_candle={'close': indicators['prev_close']}
        )
        if not entry_met:
            logger.info(f"[Trader] [{strategy_name}] {label} 진입 신호 없음: {entry_reason}")
            self._set_eval(strategy_name, False, "NO_SIGNAL", entry_reason, f"⚪ 조건 미충족 ({entry_reason})")
            return

        logger.info(f"[Trader] [{strategy_name}] {label} 진입 신호 발생: {entry_reason}")

        if cleared_today:
            # PRD 명세: 청산 완료 당일 발생한 신호는 신규 싸이클의 1차 더미로 이관 (cycle_id 추가 증가 없음)
            self.cycles.consume_dummy(strategy_name, date_str=date_str)
            self.excel.append_record(
                date_str=date_str,
                strategy_name=strategy_name,
                event_type="BUY_DUMMY",
                price=None,
                qty=None,
                unit_label="더미 1회차 (이관)",
                dummy_status="1 / 2",
                total_balance=self.last_total_balance,
                note=f"{entry_reason} (당일 청산 후 신규 싸이클 1차 더미로 이관)"
            )
            bar = format_progress_single_track(1, 0, include_counts=False)
            execution_logs.append(f"🟡 [{label} 1차 더미 이관] 당일 청산으로 신규 싸이클 1차 더미 소진: {bar}")
            self._set_eval(strategy_name, True, "DUMMY_TRANSFERRED", entry_reason, f"🟡 1차 더미 이관 ({entry_reason})")
            return

        action_type, weight, unit_label = self.cycles.determine_buy_action(strategy_name)
        if action_type == "DUMMY":
            dummy_count = self.cycles.consume_dummy(strategy_name, date_str=date_str)
            self.excel.append_record(
                date_str=date_str,
                strategy_name=strategy_name,
                event_type="BUY_DUMMY",
                price=None,
                qty=None,
                unit_label=unit_label,
                dummy_status=f"{dummy_count} / 2",
                total_balance=self.last_total_balance,
                note=f"{entry_reason} (더미 방어 소진)"
            )
            bar = format_progress_single_track(dummy_count, 0, include_counts=False)
            execution_logs.append(f"⚪ [{label} 더미 소진] {unit_label} 완료: {bar} (더미 {dummy_count}/2)")
            self._set_eval(strategy_name, True, "DUMMY_CONSUMED", entry_reason, f"⚪ {unit_label} 소진 ({entry_reason})")
            return

        self._execute_real_entry(
            strategy_name=strategy_name,
            current_price=indicators['current_price'],
            date_str=date_str,
            weight=weight,
            unit_label=unit_label,
            entry_reason=entry_reason,
            execution_logs=execution_logs
        )

    def _execute_real_entry(
        self,
        strategy_name: str,
        current_price: float,
        date_str: str,
        weight: float,
        unit_label: str,
        entry_reason: str,
        execution_logs: List[str]
    ):
        """실전 진입: 유닛 산정 → 주문 → 체결 확인 → 상태/엑셀 기록 → 상대 더미 리셋 → 잔고 재조회"""
        cfg = SIDE_CFG[strategy_name]
        label = cfg["label"]
        opposite = cfg["opposite"]

        # 상대편 포지션 보유 여부에 따른 동적 유닛 기준 (Hedge 모드 양방향 상태)
        opposite_qty = float(self.cycles.get_strategy_state(opposite).get("total_qty", 0.0))
        if opposite_qty > 0:
            unit_base_usd = self.last_free_margin / UNIT_DIVISOR
            basis_label = "가용 잔고(헤지)"
        else:
            unit_base_usd = self.last_total_balance / UNIT_DIVISOR
            basis_label = "총 평가 잔고(단일)"

        target_usd = unit_base_usd * weight

        # 최소 주문금액 가드
        if target_usd < MIN_ORDER_USD:
            msg = (
                f"⚠️ [{label} 주문 스킵] {unit_label}: 주문금액(${target_usd:,.2f})이 "
                f"최소 한도(${MIN_ORDER_USD:,.2f}) 미만입니다. ({basis_label} 기준)"
            )
            logger.warning(f"[Trader] {msg}")
            execution_logs.append(msg)
            self._set_eval(
                strategy_name, True, "SKIPPED_MIN_ORDER",
                f"{entry_reason} (주문금액 ${target_usd:,.2f} < 최소한도 ${MIN_ORDER_USD:,.2f})",
                f"⚠️ 주문 스킵 (주문금액 ${target_usd:,.1f} < ${MIN_ORDER_USD:,.1f})"
            )
            return

        # 1) 주문 → 체결 확인
        target_qty = target_usd / current_price if current_price > 0 else 0.0
        result = self.client.create_hedge_order(
            side=cfg["open_side"],
            amount=target_qty,
            pos_side=cfg["pos_side"],
            is_close=False,
            ref_price=current_price
        )
        fill_price = result.average if result.average > 0 else current_price
        invested_usd = fill_price * result.filled

        # 2) 체결 기준 상태 기록
        self.cycles.record_executed_unit(
            strategy_name=strategy_name,
            price=fill_price,
            qty=result.filled,
            date_str=date_str
        )

        # 3) 엑셀 기록
        self.excel.append_record(
            date_str=date_str,
            strategy_name=strategy_name,
            event_type="BUY_UNIT",
            price=fill_price,
            qty=result.filled,
            unit_label=unit_label,
            dummy_status="2 / 2",
            total_balance=self.last_total_balance,
            note=f"{entry_reason} (투입: ${invested_usd:,.2f}, {basis_label}, 수수료: ${result.fee:,.4f})"
        )

        executed = self.cycles.get_strategy_state(strategy_name).get("executed_units", 0)
        bar = format_progress_single_track(2, executed, include_counts=False)
        execution_logs.append(
            f"{cfg['emoji']} [{cfg['entry_title']}] {unit_label}: ${fill_price:,.2f}에 {result.filled:,.4f} Qty "
            f"(${invested_usd:,.1f}) [{basis_label}] | {bar} ({executed}/10)"
        )
        self._set_eval(
            strategy_name, True, "BUY_EXECUTED", entry_reason,
            f"{cfg['emoji']} {unit_label} {cfg['entry_summary']} ({entry_reason})"
        )

        # 4) 체결 직후: 상대 전략의 미완성 더미 초기화 트리거
        if self.cycles.reset_incomplete_dummy(opposite, trigger_source=strategy_name, date_str=date_str):
            bar_reset = format_progress_single_track(0, 0, include_counts=False)
            opp_label = cfg["opp_label"]
            execution_logs.append(
                f"🔄 [{opp_label} 더미 초기화] {label} 실전 매수 체결로 {opp_label} 미완성 더미 리셋: {bar_reset} (더미 0/2)"
            )
            self.excel.append_record(
                date_str=date_str,
                strategy_name=opposite,
                event_type="RESET_DUMMY",
                price=None,
                qty=None,
                unit_label="더미 초기화 (0/2)",
                dummy_status="0 / 2",
                total_balance=self.last_total_balance,
                note=f"{label} 실전 매수 체결에 따른 {opp_label} 미완성 더미 0/2 초기화"
            )

        # 5) 다음 전략 유닛 산정을 위한 잔고 재조회 (실패해도 이미 체결된 주문 기록은 유지)
        try:
            self._refresh_balance()
        except Exception as e:
            logger.warning(f"[Trader] 체결 후 잔고 재조회 실패 (이전 잔고 값 유지): {e}")

    # ------------------------------------------------------------------
    # 5분 주기 2단계 트레일링 감시
    # ------------------------------------------------------------------
    def process_stage2_monitoring(self, df_4h: pd.DataFrame, blocked: Optional[Set[str]] = None) -> List[str]:
        """
        5분 주기 2단계 트레일링 매도 전용 감시 파이프라인
        A 또는 B 전략이 trailing_mode 활성 상태일 때 4시간봉 5MA 및 최소 이익 보존선을 검사하여 청산 집행
        """
        blocked = set(blocked or set())
        execution_logs: List[str] = []
        if df_4h.empty or len(df_4h) < 5:
            logger.warning("[Trader 4H] 4시간봉 캔들이 부족하여 2단계 감시를 건너뜁니다.")
            return execution_logs

        indicators_4h = IndicatorCalculator.calculate_4h(df_4h)
        current_price = indicators_4h['current_price']
        date_str = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')   # 실제 청산 시각(KST)
        self._refresh_balance()

        for name in STRATEGY_NAMES:
            state = self.cycles.get_strategy_state(name)
            if not state.get("trailing_mode", False):
                continue
            cfg = SIDE_CFG[name]
            label = cfg["label"]
            if name in blocked:
                logger.warning(f"[Trader 4H] [{name}] 포지션 불일치로 2단계 감시 청산을 생략합니다.")
                continue

            try:
                exit_met, reason_2 = self.strategies[name].check_stage2_exit_signal(
                    current_price=current_price,
                    indicators_4h=indicators_4h,
                    trailing_base_price=state.get("trailing_base_price", 0.0),
                    buffer_pct=PRESERVATION_BUFFER_PCT
                )
                if not exit_met:
                    logger.info(f"[Trader 4H] [{name}] {label} 2단계 유지: {reason_2}")
                    continue

                logger.info(f"[Trader 4H] [{name}] {label} 2단계 트레일링 청산 조건 충족! {reason_2}")
                closed, log_msg = self._close_position(
                    strategy_name=name,
                    ref_price=current_price,
                    date_str=date_str,
                    reason=reason_2,
                    unit_label="ALL_CLEAR_TRAILING",
                    log_title=f"{label} 2단계 트레일링 익절 완료",
                    note_prefix="2단계 트레일링 청산: "
                )
                execution_logs.append(log_msg)

                if closed:
                    last_state = self.cycles.get_strategy_state(name)
                    realized_pnl = last_state.get('last_realized_pnl', 0.0)
                    realized_pct = last_state.get('last_realized_pct', 0.0)
                    kst_str = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')
                    unit_base = self.last_total_balance / UNIT_DIVISOR
                    msg = (
                        f"<b>📊 [Bitget V3 Switch 2단계 {label} 청산 보고]</b>\n\n"
                        f"• <b>기준 일시:</b> {kst_str}\n"
                        f"• <b>종목:</b> {self.client.symbol}\n"
                        f"• <b>계좌 총 평가 잔고:</b> ${self.last_total_balance:,.2f}\n"
                        f"• <b>1.0 Unit 기본 금액:</b> ${unit_base:,.2f}\n"
                        f"• <b>청산 내용:</b> {self._escape(log_msg)}\n"
                        f"• <b>확정 수익금:</b> <b>{realized_pnl:+,.2f} USD ({realized_pct:+.2f}%)</b>"
                    )
                    self.notifier.send_message(msg)
            except OrderSkipError as e:
                logger.warning(f"[Trader 4H] [{name}] {label} 2단계 청산 주문 스킵: {e}")
                execution_logs.append(f"⚠️ [{label} 2단계 청산 주문 스킵] {e}")
            except Exception as e:
                logger.error(f"[Trader 4H] [{name}] {label} 2단계 청산 실패: {e}", exc_info=True)
                execution_logs.append(f"❌ [{label} 2단계 청산 실패] {e}")
                if should_send_alert(f"stage2_close_fail_{name}", ALERT_COOLDOWN_SEC):
                    self.notifier.send_message(
                        f"<b>🚨 [Bitget V3 Switch 2단계 {label} 청산 실패]</b>\n"
                        f"• <b>사유:</b> {self._escape(str(e))}\n"
                        f"• 다음 5분 틱에서 자동 재시도합니다."
                    )

        return execution_logs


trader = Trader()
```

### (4) 의존성/주의
- **main.py 호환**: `trader.last_total_balance`, `last_free_margin`, `last_unit_base_usd`, `last_eval_a`, `last_eval_b`는 유지됩니다(`last_eval_a/b`는 property). `SIDE_CFG`는 Step 10의 main에서 import 합니다.
- `process_daily_cycle()`, `process_stage2_monitoring()`에 `blocked` 인자가 추가되었습니다 (기본값 `None`이라 기존 호출도 동작).
- 진입 수수료는 엑셀 비고에만 기록되고 실현손익에는 **청산 수수료만** 차감됩니다 (알려진 한계, 필요 시 별도 요청).
- `strategy.check_stage2_exit_signal(...)` 호출 형식(키워드 인자 4개, 여러 줄)은 Phase 2 Diff의 앵커이므로 **그대로 유지**합니다.

### Step 검증
```powershell
python -m py_compile manager/trader.py
```

---

## Step 10. `main.py` (전체 교체)

### (1) 파일 경로
`main.py`

### (2) 기존 코드 영역
파일 전체 (L1-L318). 핵심 변경:
- L39-57 `generate_mock_ohlcv()` 삭제 → `utils.mock_data`에서 import
- L88-95, L242-249 모의 데이터 폴백 → `load_ohlcv_or_abort()` (실전 예외)
- 시작 시 `validate_live_config()`, state 손상 검사, 포지션 대조, 2단계 오류 경보 추가
- CLI 추가: `--reconcile`, `--adopt-exchange`, `--test-order {long,short}`

### (3) 코드 (전체)
```python
"""
main.py
bitget_v3_switch 2단계 스마트 트레일링 자동매매 시스템 메인 엔트리 포인트
- 일봉 마감 평가: 매일 09:10 ~ 09:15 KST 사이 1회 실행 (Crontab 3-59/5 기준 09:13 틱)
- 4시간봉 2단계 트레일링 감시: 5분 주기(Crontab 또는 백그라운드 루프) 실행
- 실전 모드(PAPER_TRADING=false)에서는 모의 데이터/모의 잔고로 절대 매매하지 않습니다.
"""

import argparse
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Dict

import pandas as pd

from config import (
    SYMBOL,
    TIMEFRAME,
    CANDLE_LIMIT,
    TIMEFRAME_4H,
    CANDLE_LIMIT_4H,
    DAILY_EVAL_HOUR_KST,
    DAILY_EVAL_START_MIN,
    DAILY_EVAL_END_MIN,
    PAPER_TRADING,
    UNIT_DIVISOR,
    MIN_ORDER_USD,
    ALERT_COOLDOWN_SEC,
    validate_live_config,
)
from api.exceptions import TradingError, DataUnavailableError, StateCorruptedError
from utils.logger import logger
from utils.notifier import notifier
from utils.excel_logger import excel_logger
from utils.formatters import format_progress_single_track
from utils.mock_data import generate_mock_ohlcv
from utils.alert_throttle import should_send_alert
from api.bitget_client import bitget_client
from strategy.indicator import IndicatorCalculator
from manager.cycle_manager import cycle_manager
from manager.reconciler import reconciler, ReconcileResult
from manager.trader import trader, SIDE_CFG

KST = timezone(timedelta(hours=9))
MIN_DAILY_CANDLES = 61   # 일봉 지표 산출 최소 캔들 수
MIN_4H_CANDLES = 5       # 4시간봉 지표 산출 최소 캔들 수


def escape_tg(text: str) -> str:
    """텔레그램 HTML 파싱 오류 방지를 위한 특수문자 이스케이프"""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_live_halt_alert(stage: str, reason: str):
    """실전 매매 중단 경보 (설정/데이터/상태 오류)"""
    kst_err = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')
    notifier.send_message(
        f"<b>🚨 [Bitget V3 Switch LIVE 매매 중단]</b>\n"
        f"• <b>발생 일시:</b> {kst_err}\n"
        f"• <b>단계:</b> {escape_tg(stage)}\n"
        f"• <b>사유:</b> {escape_tg(reason)}\n"
        f"• <b>조치:</b> logs/trading.log 확인 후 원인 해결"
    )


def load_ohlcv_or_abort(timeframe: str, limit: int, min_len: int) -> pd.DataFrame:
    """
    OHLCV를 수집합니다.
    - 실전: 수집 실패/캔들 부족 시 DataUnavailableError (모의 데이터 대체 금지)
    - 모의: 수집 실패/캔들 부족 시 모의 데이터로 대체
    """
    try:
        df = bitget_client.fetch_ohlcv(symbol=SYMBOL, timeframe=timeframe, limit=limit)
    except Exception as e:
        if PAPER_TRADING:
            logger.warning(f"[PAPER] {timeframe} 캔들 수집 실패 ({e}) → 모의 데이터로 진행합니다.")
            return generate_mock_ohlcv(limit, timeframe=timeframe)
        raise DataUnavailableError(f"[LIVE] {timeframe} 캔들 수집 실패: {e}") from e

    if df.empty or len(df) < min_len:
        if PAPER_TRADING:
            logger.warning(f"[PAPER] {timeframe} 캔들 부족({len(df)} < {min_len}) → 모의 데이터로 진행합니다.")
            return generate_mock_ohlcv(limit, timeframe=timeframe)
        raise DataUnavailableError(f"[LIVE] {timeframe} 캔들 부족 ({len(df)} < {min_len})")
    return df


def is_daily_evaluation_window() -> bool:
    """현재 한국 시각(KST)이 일봉 마감 평가 시간대(09:10 ~ 09:15)인지 검사합니다."""
    kst_now = datetime.now(KST)
    return (
        kst_now.hour == DAILY_EVAL_HOUR_KST and
        DAILY_EVAL_START_MIN <= kst_now.minute <= DAILY_EVAL_END_MIN
    )


def format_reconcile_summary(results: Dict[str, ReconcileResult]) -> str:
    """포지션 대조 결과 한 줄 요약"""
    if not results:
        return "대조 미실행"
    parts = []
    for name, r in results.items():
        label = SIDE_CFG[name]["label"]
        if r.status == "MISMATCH":
            parts.append(f"{label} ⛔MISMATCH(state {r.state_qty:,.4f} / 거래소 {r.exchange_qty:,.4f})")
        else:
            parts.append(f"{label} {r.status}")
    return " / ".join(parts)


def run_reconciliation(stage: str, throttle: bool) -> Dict[str, ReconcileResult]:
    """포지션 대조 실행 및 불일치 경보 (throttle=True면 동일 경보 쿨다운 적용)"""
    results = reconciler.reconcile()
    for name, r in results.items():
        if not r.blocked:
            continue
        if throttle and not should_send_alert(f"mismatch_{name}", ALERT_COOLDOWN_SEC):
            continue
        notifier.send_message(
            f"<b>⛔ [Bitget V3 Switch 포지션 불일치]</b>\n"
            f"• <b>단계:</b> {escape_tg(stage)}\n"
            f"• <b>전략:</b> {name} ({SIDE_CFG[name]['label']})\n"
            f"• <b>내용:</b> {escape_tg(r.detail)}\n"
            f"• <b>조치:</b> 해당 전략 매매 차단 중. python main.py --reconcile 로 확인 후 필요 시 --adopt-exchange"
        )
    return results


def run_pipeline() -> bool:
    """
    일봉 마감 시점 통합 실행 파이프라인 (09:10~09:15 KST)
    0. state.json 무결성 확인
    1. 거래소 환경 점검/검증
    2. 일봉 OHLCV 수집 (실전 실패 시 중단)
    3. 거래소 실제 포지션 대조 (불일치 전략 매매 차단)
    4. 지표 산출 → 5. 1차 청산/진입 평가 → 6. 텔레그램 일일 보고서 발송
    """
    start_time = datetime.now()
    logger.info("=" * 70)
    logger.info(f"[Pipeline] 일봉 마감 실행 파이프라인 시작 ({start_time.strftime('%Y-%m-%d %H:%M:%S')})")
    logger.info("=" * 70)

    try:
        # 0. state.json 무결성 확인 (실전 모드 손상 시 중단)
        if cycle_manager.load_error:
            raise StateCorruptedError(cycle_manager.load_error)

        # 1. 거래소 환경 점검 (실전: 헤지모드/마진/레버리지 검증 실패 시 예외)
        bitget_client.setup_exchange()

        # 2. 일봉 OHLCV 수집
        df = load_ohlcv_or_abort(TIMEFRAME, CANDLE_LIMIT, MIN_DAILY_CANDLES)

        # 3. 거래소 실제 포지션 대조
        recon_results = run_reconciliation(stage="일봉 파이프라인", throttle=False)
        blocked = {name for name, r in recon_results.items() if r.blocked}

        # 4. 싸이클 최고/최저가 로드 및 일봉 지표 산출
        state_a = cycle_manager.get_strategy_state("strategy_A")
        state_b = cycle_manager.get_strategy_state("strategy_B")
        indicators = IndicatorCalculator.calculate(
            df=df,
            cycle_peak=state_a.get("cycle_peak", 0.0),
            cycle_trough=state_b.get("cycle_trough", 0.0)
        )

        # 5. Trader 주문 집행 및 1차 청산 / 매수 평가
        logs = trader.process_daily_cycle(indicators, blocked=blocked)

        # 6. 상태 요약 및 텔레그램 발송
        updated_state_a = cycle_manager.get_strategy_state("strategy_A")
        updated_state_b = cycle_manager.get_strategy_state("strategy_B")

        trailing_info_a = " [2단계 트레일링 감시 중]" if updated_state_a.get("trailing_mode") else ""
        trailing_info_b = " [2단계 트레일링 감시 중]" if updated_state_b.get("trailing_mode") else ""

        total_balance = trader.last_total_balance
        free_margin = trader.last_free_margin
        unit_base_usd = trader.last_unit_base_usd
        hedge_unit_usd = free_margin / UNIT_DIVISOR

        kst_now = datetime.now(KST)
        kst_str = kst_now.strftime('%Y-%m-%d %H:%M:%S KST')
        candle_date_raw = indicators.get('candle_date', '')
        candle_date_display = f"{candle_date_raw} (KST)" if candle_date_raw else f"{kst_now.strftime('%Y-%m-%d')} (KST)"

        current_price = indicators['current_price']

        # [전략 A - Long] 평가손익 및 실현손익
        qty_a = updated_state_a.get('total_qty', 0.0)
        avg_a = updated_state_a.get('avg_price', 0.0)
        if qty_a > 0 and avg_a > 0:
            pnl_usd_a = (current_price - avg_a) * qty_a
            pnl_pct_a = ((current_price / avg_a) - 1.0) * 100.0
            pnl_str_a = f"<b>{pnl_usd_a:+,.2f} USD ({pnl_pct_a:+.2f}%)</b>"
        else:
            pnl_str_a = "$0.00 USD (0.00%) [포지션 없음]"

        cum_pnl_a = updated_state_a.get('cumulative_realized_pnl', 0.0)
        last_pnl_a = updated_state_a.get('last_realized_pnl', 0.0)
        last_pct_a = updated_state_a.get('last_realized_pct', 0.0)
        last_closed_a_str = f" (직전 확정: {last_pnl_a:+,.2f} USD, {last_pct_a:+.2f}%)" if last_pnl_a != 0 else ""

        # [전략 B - Short] 평가손익 및 실현손익
        qty_b = updated_state_b.get('total_qty', 0.0)
        avg_b = updated_state_b.get('avg_price', 0.0)
        if qty_b > 0 and avg_b > 0:
            pnl_usd_b = (avg_b - current_price) * qty_b
            pnl_pct_b = ((avg_b - current_price) / avg_b) * 100.0
            pnl_str_b = f"<b>{pnl_usd_b:+,.2f} USD ({pnl_pct_b:+.2f}%)</b>"
        else:
            pnl_str_b = "$0.00 USD (0.00%) [포지션 없음]"

        cum_pnl_b = updated_state_b.get('cumulative_realized_pnl', 0.0)
        last_pnl_b = updated_state_b.get('last_realized_pnl', 0.0)
        last_pct_b = updated_state_b.get('last_realized_pct', 0.0)
        last_closed_b_str = f" (직전 확정: {last_pnl_b:+,.2f} USD, {last_pct_b:+.2f}%)" if last_pnl_b != 0 else ""

        # 차수 진행 상태 인디케이터
        progress_a = format_progress_single_track(
            dummy_count=updated_state_a.get('dummy_count', 0),
            executed_units=updated_state_a.get('executed_units', 0)
        )
        progress_b = format_progress_single_track(
            dummy_count=updated_state_b.get('dummy_count', 0),
            executed_units=updated_state_b.get('executed_units', 0)
        )

        # 당일 전략별 판정 사유
        desc_a = escape_tg(trader.last_eval_a.get('summary', '조건 판정 정보 없음'))
        desc_b = escape_tg(trader.last_eval_b.get('summary', '조건 판정 정보 없음'))

        summary_msg = (
            f"<b>📊 [Bitget V3 Switch 일봉 마감 보고]</b>\n\n"
            f"• <b>기준 일시:</b> {kst_str}\n"
            f"• <b>종목:</b> {SYMBOL}\n"
            f"• <b>계좌 총 평가 잔고:</b> ${total_balance:,.2f} (가용: ${free_margin:,.2f})\n"
            f"• <b>1.0 Unit 기본 금액:</b> 단일 ${unit_base_usd:,.2f} / 헤지 ${hedge_unit_usd:,.2f}\n"
            f"• <b>포지션 대조:</b> {escape_tg(format_reconcile_summary(recon_results))}\n"
            f"• <b>일봉 기준일:</b> {candle_date_display} (전일 마감 완성봉)\n"
            f"• <b>실시간 현재가:</b> ${current_price:,.2f} (기준봉 종가: ${indicators.get('eval_close', current_price):,.2f}, "
            f"캔들: {indicators['candle_change']*100:+.2f}%, 전일비: {indicators['daily_change']*100:+.2f}%)\n"
            f"• <b>SMA5:</b> ${indicators['sma5']:,.2f} | <b>SMA60:</b> ${indicators['sma60']:,.2f} "
            f"({'상승' if indicators['is_sma60_rising'] else '하락'})\n\n"
            f"<b>[전략 A - Long DCA]{trailing_info_a}</b>\n"
            f"• 싸이클 #{updated_state_a.get('cycle_id')} | 보유: {qty_a:,.4f} Qty\n"
            f"• <b>매수 차수:</b> {progress_a}\n"
            f"• 평단가: ${avg_a:,.2f} | <b>평가손익:</b> {pnl_str_a}\n"
            f"• <b>누적 실현손익:</b> {cum_pnl_a:+,.2f} USD{last_closed_a_str}\n\n"
            f"<b>[전략 B - Short DCA]{trailing_info_b}</b>\n"
            f"• 싸이클 #{updated_state_b.get('cycle_id')} | 보유: {qty_b:,.4f} Qty\n"
            f"• <b>매수 차수:</b> {progress_b}\n"
            f"• 평단가: ${avg_b:,.2f} | <b>평가손익:</b> {pnl_str_b}\n"
            f"• <b>누적 실현손익:</b> {cum_pnl_b:+,.2f} USD{last_closed_b_str}\n\n"
            f"<b>[금일 집행 내역]</b>\n"
            f"• <b>전략 A (롱):</b> {desc_a}\n"
            f"• <b>전략 B (숏):</b> {desc_b}\n"
        )
        if logs:
            summary_msg += "\n".join(f"• {escape_tg(log)}" for log in logs)
        else:
            summary_msg += "• (금일 실제 주문 집행 없음)"

        notifier.send_message(summary_msg)
        try:
            excel_logger.update_pnl_chart()
        except Exception as e_chart:
            logger.warning(f"[Pipeline] 엑셀 손익차트 갱신 실패: {e_chart}")

        logger.info(f"[Pipeline] 일봉 마감 실행 파이프라인 정상 종료 (소요시간: {datetime.now() - start_time})")
        return True

    except Exception as e:
        logger.error(f"[Pipeline] 파이프라인 실행 중 심각한 예외 발생: {e}", exc_info=True)
        if isinstance(e, TradingError):
            send_live_halt_alert("일봉 파이프라인", str(e))
        else:
            kst_err = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')
            notifier.send_message(
                f"<b>🚨 [Bitget V3 Switch 에러 발생]</b>\n• <b>발생 일시:</b> {kst_err}\n• <b>내용:</b> {escape_tg(str(e))}"
            )
        return False


def run_stage2_pipeline() -> bool:
    """
    5분 주기 2단계 트레일링 매도 전용 감시 파이프라인
    - A 또는 B 전략이 trailing_mode 활성 중일 때만 4시간봉 5MA 및 최소 이익 보존선 판정
    - 오류 시 텔레그램 경보 (동일 오류 1시간 쿨다운)
    """
    if not cycle_manager.is_any_trailing_active():
        return True

    logger.info("[Pipeline 4H] 2단계 트레일링 매도 감시 파이프라인 시작")
    try:
        if cycle_manager.load_error:
            raise StateCorruptedError(cycle_manager.load_error)

        bitget_client.setup_exchange()
        df_4h = load_ohlcv_or_abort(TIMEFRAME_4H, CANDLE_LIMIT_4H, MIN_4H_CANDLES)

        recon_results = run_reconciliation(stage="2단계 감시", throttle=True)
        blocked = {name for name, r in recon_results.items() if r.blocked}

        logs = trader.process_stage2_monitoring(df_4h, blocked=blocked)
        if logs:
            logger.info(f"[Pipeline 4H] 2단계 트레일링 처리 결과: {len(logs)}건")
        return True
    except Exception as e:
        logger.error(f"[Pipeline 4H] 2단계 감시 파이프라인 오류: {e}", exc_info=True)
        if should_send_alert("stage2_pipeline_error", ALERT_COOLDOWN_SEC):
            send_live_halt_alert("2단계 트레일링 감시", str(e))
        return False


def run_cron_tick() -> bool:
    """
    Crontab 5분 주기 통합 틱 핸들러 (권장: 3-59/5 * * * *)
    - 09:10 ~ 09:15 KST: 하루 1회 일봉 마감 평가 및 매수 진입 실행
    - 그 외 시간대:
      - 2단계 트레일링 활성 중일 때: 4시간봉 2단계 매도 감시
      - 2단계 트레일링 비활성 시: 즉시 종료
    """
    kst_now = datetime.now(KST)
    logger.info(f"[CronTick] 5분 주기 감시 틱 실행 (KST {kst_now.strftime('%Y-%m-%d %H:%M:%S')})")

    if is_daily_evaluation_window():
        logger.info("[CronTick] 일봉 마감 평가 시간대(09:10~09:15 KST) 충족 -> 일봉 마감 파이프라인 실행")
        return run_pipeline()

    if cycle_manager.is_any_trailing_active():
        logger.info("[CronTick] 2단계 트레일링 감시 활성 상태 -> 4시간봉 감시 파이프라인 실행")
        return run_stage2_pipeline()

    logger.info("[CronTick] 2단계 트레일링 미활성 및 일봉 평가 시간대 아님 -> 즉시 종료")
    return True


def run_reconcile_cli(adopt: bool) -> int:
    """--reconcile [--adopt-exchange] 수동 대조/복구 CLI"""
    if PAPER_TRADING:
        print("[Reconcile] 모의 매매(PAPER_TRADING=true) 모드에서는 거래소 대조가 필요 없습니다.")
        return 0

    bitget_client.setup_exchange()
    if cycle_manager.load_error and not adopt:
        print(f"[Reconcile] state.json 손상 상태입니다: {cycle_manager.load_error}")
        print("[Reconcile] 복구하려면 --reconcile --adopt-exchange 를 실행하세요.")
        return 1

    results = reconciler.reconcile(auto_sync=False)
    for r in results.values():
        print(
            f"[{r.strategy}] {r.status} | state {r.state_qty:,.4f} @ ${r.state_avg:,.4f} | "
            f"거래소 {r.exchange_qty:,.4f} @ ${r.exchange_avg:,.4f} | {r.detail}"
        )

    if not adopt:
        return 1 if any(r.blocked for r in results.values()) else 0

    answer = input("거래소 포지션 값을 state.json에 채택합니다. 계속하시겠습니까? (y/N): ").strip().lower()
    if answer != "y":
        print("[Reconcile] 취소되었습니다.")
        return 1

    date_str = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')
    adopted = reconciler.adopt_exchange(date_str)
    for r in adopted.values():
        print(f"[{r.strategy}] 채택 결과: {r.status} - {r.detail}")
    return 0


def run_test_order(side_key: str) -> int:
    """--test-order long|short: 최소 금액 실주문 진입 후 즉시 청산 (state/엑셀 미기록)"""
    if PAPER_TRADING:
        print("[TestOrder] PAPER_TRADING=true 상태입니다. 실주문 테스트는 PAPER_TRADING=false 에서만 실행됩니다.")
        return 1

    name = "strategy_A" if side_key == "long" else "strategy_B"
    cfg = SIDE_CFG[name]
    bitget_client.setup_exchange()

    long_pos, short_pos = bitget_client.fetch_open_positions()
    existing = long_pos if side_key == "long" else short_pos
    if float(existing.get("contracts", 0.0)) > 0:
        print("[TestOrder] 해당 방향에 이미 포지션이 있어 테스트를 중단합니다 (평단 왜곡 방지).")
        return 1

    price = float(bitget_client.exchange.fetch_ticker(SYMBOL)['last'])
    qty = max(bitget_client.get_min_amount(), (MIN_ORDER_USD * 1.4) / price)
    print(f"[TestOrder] {side_key.upper()} 진입 후 즉시 청산: 약 {qty:.4f} Qty (≈ ${qty * price:,.2f}) @ ${price:,.2f}")
    if input("실제 주문을 실행합니다. 계속하시겠습니까? (y/N): ").strip().lower() != "y":
        print("[TestOrder] 취소되었습니다.")
        return 1

    opened = bitget_client.create_hedge_order(
        side=cfg["open_side"], amount=qty, pos_side=cfg["pos_side"], is_close=False, ref_price=price
    )
    print(f"[TestOrder] 진입 체결: {opened.filled} @ ${opened.average:,.4f} (수수료 ${opened.fee:,.4f}, 주문 {opened.order_id})")
    try:
        closed = bitget_client.create_hedge_order(
            side=cfg["close_side"], amount=opened.filled, pos_side=cfg["pos_side"], is_close=True, ref_price=price
        )
    except Exception as e:
        print(f"[TestOrder] ❌ 청산 실패: {e} → Bitget 앱에서 테스트 포지션을 수동으로 청산하세요.")
        return 1
    print(f"[TestOrder] 청산 체결: {closed.filled} @ ${closed.average:,.4f} (수수료 ${closed.fee:,.4f}, 주문 {closed.order_id})")
    print("[TestOrder] state.json/엑셀에는 기록하지 않았습니다. Bitget 앱에서 해당 방향 포지션 0을 확인하세요.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Bitget V3 Switch 2단계 스마트 트레일링 자동매매 파이프라인")
    parser.add_argument("--run-once", action="store_true", help="일봉 마감 파이프라인을 1회 즉시 실행하고 종료합니다.")
    parser.add_argument("--cron", action="store_true", help="크론탭(3-59/5 * * * *) 5분 주기 틱을 1회 실행하고 종료합니다.")
    parser.add_argument("--stage2", action="store_true", help="4시간봉 2단계 트레일링 감시를 1회 즉시 실행하고 종료합니다.")
    parser.add_argument("--loop", action="store_true", help="5분 주기 상시 백그라운드 스케줄러로 구동합니다.")
    parser.add_argument("--update-chart", action="store_true", help="trade_history.xlsx의 손익차트(종합/V3+/V3-) 시트를 즉시 갱신하고 종료합니다.")
    parser.add_argument("--reconcile", action="store_true", help="state.json과 거래소 실제 포지션을 대조하여 출력합니다.")
    parser.add_argument("--adopt-exchange", action="store_true", help="--reconcile과 함께 사용: 거래소 포지션 값을 state.json에 채택합니다.")
    parser.add_argument("--test-order", choices=["long", "short"], help="최소 금액 실주문 진입 후 즉시 청산 테스트 (state 미기록)")
    args = parser.parse_args()

    if args.update_chart:
        logger.info("[Main] --update-chart 모드로 손익차트 시트 갱신을 실행합니다.")
        excel_logger.update_pnl_chart()
        sys.exit(0)

    # 실전 모드 필수 설정 검증 (누락 시 어떤 매매 경로도 실행하지 않음)
    missing = validate_live_config()
    if missing:
        msg = f"실전 모드(PAPER_TRADING=false)인데 필수 API 설정이 비어 있습니다: {', '.join(missing)}"
        logger.critical(f"[Main] {msg}")
        send_live_halt_alert("시작 설정 검증", msg)
        sys.exit(2)

    if args.reconcile:
        sys.exit(run_reconcile_cli(adopt=args.adopt_exchange))
    elif args.test_order:
        sys.exit(run_test_order(args.test_order))
    elif args.cron:
        logger.info("[Main] --cron 모드로 5분 주기 틱을 실행합니다.")
        success = run_cron_tick()
        sys.exit(0 if success else 1)
    elif args.stage2:
        logger.info("[Main] --stage2 모드로 2단계 트레일링 감시를 즉시 실행합니다.")
        success = run_stage2_pipeline()
        sys.exit(0 if success else 1)
    elif args.loop:
        logger.info("[Main] --loop 5분 상시 백그라운드 스케줄러 구동을 시작합니다.")
        run_cron_tick()
        while True:
            time.sleep(300)
            run_cron_tick()
    else:
        logger.info("[Main] --run-once 단일 일봉 실행 모드로 파이프라인을 시작합니다.")
        success = run_pipeline()
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
```

### (4) 의존성/주의
- import 대상: `api.exceptions`(Step 1), `config.validate_live_config/ALERT_COOLDOWN_SEC`(Step 2), `utils.mock_data`(Step 3), `utils.alert_throttle`(Step 4), `manager.reconciler`(Step 8), `manager.trader.SIDE_CFG`(Step 9).
- `MIN_DAILY_CANDLES = 61`, `MIN_4H_CANDLES = 5`, `--loop` 블록은 Phase 2/3 Diff의 앵커이므로 **이 텍스트 그대로** 작성합니다.
- `--update-chart`는 매매와 무관하므로 설정 검증보다 먼저 실행됩니다.

### Step 검증
```powershell
python -m py_compile main.py
python -c "import main; print('import ok')"
```

---

## Step 11. `.gitignore` (Diff)

### (1) 파일 경로
`.gitignore`

### (2) 기존 코드 영역
L45-48 `# Runtime Data & State` 블록

### (3) 변경 코드
```diff
 # Runtime Data & State
 state.json
+state.bak.json
+state.corrupt_*.json
 *.tmp
 *.xlsx
```

### (4) 의존성/주의
- 없음.

---

## Step 12. 통합 검증

### 12-1. 정적 검사 (필수)
```powershell
python -m py_compile config.py main.py api/exceptions.py api/bitget_client.py manager/cycle_manager.py manager/reconciler.py manager/trader.py utils/mock_data.py utils/alert_throttle.py utils/excel_logger.py
python -c "import main; print('import ok')"
```

### 12-2. PAPER 드라이런 (state/엑셀 백업 후 복원)
> 실제 `state.json`·`trade_history.xlsx`가 변경되고 텔레그램이 발송되므로 **백업 후 복원**합니다.
```powershell
Copy-Item state.json state.json.p1test; Copy-Item trade_history.xlsx trade_history.xlsx.p1test
$env:PAPER_TRADING = "true"
python main.py --run-once; "exit=$LASTEXITCODE"
python main.py --stage2; "exit=$LASTEXITCODE"
Remove-Item Env:PAPER_TRADING
Copy-Item state.json.p1test state.json -Force; Copy-Item trade_history.xlsx.p1test trade_history.xlsx -Force
Remove-Item state.json.p1test, trade_history.xlsx.p1test
```
기대 결과: `exit=0`, 로그에 `[BitgetClient] [PAPER TRADING]`(주문 발생 시), 보고서에 `포지션 대조: 롱 SKIPPED_PAPER / 숏 SKIPPED_PAPER`.

### 12-3. 실전 설정 누락 차단 (주문 미발생)
```powershell
$env:PAPER_TRADING = "false"; $env:BITGET_API_KEY = " "
python main.py --cron; "exit=$LASTEXITCODE"
Remove-Item Env:PAPER_TRADING, Env:BITGET_API_KEY
```
기대 결과: `exit=2`, 로그 `필수 API 설정이 비어 있습니다: BITGET_API_KEY`.

### 12-4. 실전 읽기 전용 대조 (사용자 수행 권장)
```powershell
python main.py --reconcile
```
기대 결과: `계정 설정 검증 통과: posMode=hedge_mode ...`, 롱/숏 `OK`.

### 12-5. 소액 실주문 테스트 (사용자 직접 수행)
```powershell
python main.py --test-order long
python main.py --test-order short
```
기대 결과: 진입/청산 체결 출력, Bitget 앱에서 포지션 0 확인.

### 롤백
미커밋 상태이므로 `git checkout -- config.py main.py api/bitget_client.py manager/cycle_manager.py manager/trader.py utils/excel_logger.py .gitignore` 후 신규 파일(`api/exceptions.py`, `utils/mock_data.py`, `utils/alert_throttle.py`, `manager/reconciler.py`)을 삭제합니다.
