"""
api/bitget_client.py
ccxt 기반 Bitget 선물 API 클라이언트 Wrapper
Hedge Mode 강제, 1X 레버리지, 교차마진 설정 및 주문/잔고/OHLCV 수집을 담당합니다.
"""

from typing import Dict, Any, Optional, Tuple
from datetime import timezone, timedelta
from pathlib import Path
import json
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
    MARGIN_MODE
)
from utils.logger import logger


class BitgetClient:
    """
    Bitget 선물(Swap) API Wrapper
    """
    def __init__(self):
        self.paper_trading = PAPER_TRADING
        self.symbol = SYMBOL
        self.exchange = self._init_exchange()

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

    def setup_exchange(self):
        """
        거래소 레버리지(1X), 마진모드(Cross), 포지션모드(Hedge) 환경을 설정 및 검증합니다.
        """
        if self.paper_trading:
            logger.info("[BitgetClient] 모의 매매(PAPER_TRADING=True) 모드로 동작 중입니다. 거래소 설정 요청을 시뮬레이션합니다.")
            return

        try:
            # 1. 헤지 모드 (Dual-side position) 설정
            try:
                self.exchange.set_position_mode(hedged=True, symbol=self.symbol)
                logger.info(f"[BitgetClient] {self.symbol} 포지션 모드: Hedge Mode (Dual-side) 설정 완료")
            except Exception as e:
                logger.debug(f"[BitgetClient] 포지션 모드 설정 결과 (이미 설정되어 있을 수 있음): {e}")

            # 2. 마진 모드 (Cross) 설정
            try:
                self.exchange.set_margin_mode(MARGIN_MODE, self.symbol)
                logger.info(f"[BitgetClient] {self.symbol} 마진 모드: {MARGIN_MODE} 설정 완료")
            except Exception as e:
                logger.debug(f"[BitgetClient] 마진 모드 설정 결과: {e}")

            # 3. 레버리지 (1X) 설정
            try:
                self.exchange.set_leverage(LEVERAGE, self.symbol)
                logger.info(f"[BitgetClient] {self.symbol} 레버리지: {LEVERAGE}X 설정 완료")
            except Exception as e:
                logger.debug(f"[BitgetClient] 레버리지 설정 결과: {e}")

        except Exception as e:
            logger.warning(f"[BitgetClient] 거래소 환경 초기화 중 경고 (API 키 또는 권한 확인 필요): {e}")

    def fetch_ohlcv(self, symbol: Optional[str] = None, timeframe: str = "1d", limit: int = 100) -> pd.DataFrame:
        """
        최근 일봉 OHLCV 데이터를 수집하여 DataFrame으로 반환합니다.
        """
        target_symbol = symbol or self.symbol
        try:
            ohlcv = self.exchange.fetch_ohlcv(target_symbol, timeframe=timeframe, limit=limit)
            if not ohlcv:
                logger.warning(f"[BitgetClient] OHLCV 데이터가 비어 있습니다: {target_symbol}")
                return pd.DataFrame()

            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            kst_tz = timezone(timedelta(hours=9))
            df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True).dt.tz_convert(kst_tz).dt.strftime('%Y-%m-%d %H:%M:%S')
            logger.info(f"[BitgetClient] {target_symbol} 일봉 {len(df)}개 수집 완료 (마지막 캔들(KST): {df['datetime'].iloc[-1]})")
            return df
        except Exception as e:
            logger.error(f"[BitgetClient] OHLCV 수집 실패 ({target_symbol}): {e}")
            raise

    def fetch_account_balance(self) -> Tuple[float, float]:
        """
        계좌의 (총 평가 잔고, 실시간 가용 잔고)를 산출하여 반환합니다.
        - total_equity: Free Margin + Position Margin + Unrealized PnL
        - free_margin: 주문 집행에 즉시 투입 가능한 여유 증거금
        모의 모드일 경우 MOCK_BALANCE 및 사용 증거금 차감액을 반환합니다.
        Returns:
            Tuple[float, float]: (total_balance, free_margin)
        """
        if self.paper_trading or not BITGET_API_KEY:
            # 모의 모드: state.json 내 보유 수량/평단가를 바탕으로 가용 잔고 시뮬레이션
            state_file = Path(__file__).resolve().parent.parent / "state.json"
            used_margin = 0.0
            if state_file.exists():
                try:
                    with open(state_file, "r", encoding="utf-8") as f:
                        st = json.load(f)
                    used_a = float(st.get("strategy_A", {}).get("total_qty", 0.0)) * float(st.get("strategy_A", {}).get("avg_price", 0.0))
                    used_b = float(st.get("strategy_B", {}).get("total_qty", 0.0)) * float(st.get("strategy_B", {}).get("avg_price", 0.0))
                    used_margin = used_a + used_b
                except Exception:
                    used_margin = 0.0
            mock_free = max(0.0, MOCK_BALANCE - used_margin)
            return MOCK_BALANCE, mock_free

        try:
            balance = self.exchange.fetch_balance({'type': 'swap'})
            usdt_info = balance.get('USDT', {})
            total_equity = float(usdt_info.get('total', 0.0))
            if total_equity <= 0:
                total_equity = float(balance.get('total', {}).get('USDT', 0.0))
            free_margin = float(usdt_info.get('free', 0.0))
            if free_margin <= 0:
                free_margin = float(balance.get('free', {}).get('USDT', 0.0))

            final_total = total_equity if total_equity > 0 else MOCK_BALANCE
            final_free = free_margin if free_margin > 0 else final_total

            logger.info(f"[BitgetClient] 실시간 잔고 조회 완료: 총 평가 ${final_total:,.2f} | 가용 ${final_free:,.2f}")
            return final_total, final_free
        except Exception as e:
            logger.error(f"[BitgetClient] 잔고 조회 실패, 모의 기본 잔고(${MOCK_BALANCE:,.2f})로 대체합니다: {e}")
            return MOCK_BALANCE, MOCK_BALANCE

    def fetch_total_balance(self) -> float:
        """
        계좌 총 평가 잔고(Free Margin + Position Margin)를 반환합니다.
        하위 호환성을 위해 유지됩니다.
        """
        total, _ = self.fetch_account_balance()
        return total

    def fetch_open_positions(self, symbol: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        현재 심볼의 Long 및 Short 포지션 정보를 튜플로 반환합니다: (long_pos, short_pos)
        """
        target_symbol = symbol or self.symbol
        default_pos = {'contracts': 0.0, 'entryPrice': 0.0, 'unrealizedPnl': 0.0, 'percentage': 0.0}

        if self.paper_trading or not BITGET_API_KEY:
            return default_pos, default_pos

        try:
            positions = self.exchange.fetch_positions([target_symbol])
            long_pos = default_pos.copy()
            short_pos = default_pos.copy()

            for pos in positions:
                side = pos.get('side', '').lower()
                contracts = float(pos.get('contracts') or pos.get('size') or 0.0)
                entry_price = float(pos.get('entryPrice') or 0.0)
                pnl = float(pos.get('unrealizedPnl') or 0.0)
                percentage = float(pos.get('percentage') or 0.0)

                pos_dict = {
                    'contracts': contracts,
                    'entryPrice': entry_price,
                    'unrealizedPnl': pnl,
                    'percentage': percentage
                }

                if side == 'long':
                    long_pos = pos_dict
                elif side == 'short':
                    short_pos = pos_dict

            return long_pos, short_pos
        except Exception as e:
            logger.error(f"[BitgetClient] 포지션 조회 실패: {e}")
            return default_pos, default_pos

    def create_hedge_order(
        self,
        side: str,              # 'buy' or 'sell'
        amount: float,          # 주문 수량 (토큰 수)
        pos_side: str,          # 'long' or 'short'
        is_close: bool = False, # 청산 주문 여부
        symbol: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Hedge Mode 주문 집행 메서드
        - 롱 진입: side='buy', pos_side='long', is_close=False
        - 롱 청산: side='sell', pos_side='long', is_close=True
        - 숏 진입: side='sell', pos_side='short', is_close=False
        - 숏 청산: side='buy', pos_side='short', is_close=True
        """
        target_symbol = symbol or self.symbol
        action_label = f"[{'청산' if is_close else '진입'}] {pos_side.upper()} {side.upper()} {amount} Qty"

        if self.paper_trading or not BITGET_API_KEY:
            logger.info(f"[BitgetClient] [PAPER TRADING] 주문 시뮬레이션: {action_label}")
            return {
                'id': 'paper_mock_order_id',
                'status': 'closed',
                'symbol': target_symbol,
                'side': side,
                'amount': amount,
                'filled': amount,
                'pos_side': pos_side,
                'is_paper': True
            }

        params = {'posSide': pos_side}
        if is_close:
            params['reduceOnly'] = True

        try:
            # 수량 정밀도 맞춤
            precision_amount = float(self.exchange.amount_to_precision(target_symbol, amount))
            logger.info(f"[BitgetClient] 실주문 발송: {action_label} (정밀도 보정 수량: {precision_amount})")
            order = self.exchange.create_order(
                symbol=target_symbol,
                type='market',
                side=side,
                amount=precision_amount,
                params=params
            )
            logger.info(f"[BitgetClient] 주문 체결 성공: ID={order.get('id')}, Status={order.get('status')}")
            return order
        except Exception as e:
            logger.error(f"[BitgetClient] 주문 실패 ({action_label}): {e}")
            raise


bitget_client = BitgetClient()
