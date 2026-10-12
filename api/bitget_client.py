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
