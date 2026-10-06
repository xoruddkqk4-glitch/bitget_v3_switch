"""
strategy/strategy_a_long.py
A 전략: Long DCA 매매 전략 모듈 (PRD 섹션 3)
하락 조건 발생 시 더미 소진 후 분할 롱 진입 -> 60일선 조건부 독립 익절
"""

from typing import Dict, Any, Tuple
from strategy.base import BaseStrategy
from config import (
    LONG_BEARISH_CANDLE_PCT,
    LONG_DAILY_DROP_PCT,
    DRAWDOWN_THRESHOLD,
    LONG_EXIT_RISING_SMA,
    LONG_EXIT_FALLING_SMA_MILD,
    LONG_EXIT_FALLING_SMA_DEEP
)


class StrategyALong(BaseStrategy):
    """
    Long DCA 전략 구현체
    """
    def __init__(self):
        super().__init__(name="strategy_A")

    def check_entry_signal(
        self,
        current_candle: Dict[str, Any],
        prev_candle: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """
        롱 진입 조건 (OR):
        1) 당일 음봉 <= -1.5% (종가 / 시가 - 1 <= -0.015)
        2) 전일 대비 등락률 <= -3.0% (종가 / 전일종가 - 1 <= -0.030)
        """
        candle_change = current_candle.get('candle_change', 0.0)
        daily_change = current_candle.get('daily_change', 0.0)

        is_bearish = candle_change <= LONG_BEARISH_CANDLE_PCT
        is_daily_drop = daily_change <= LONG_DAILY_DROP_PCT

        if is_bearish and is_daily_drop:
            return True, f"음봉 하락({candle_change*100:.2f}%) 및 전일 대비 급락({daily_change*100:.2f}%) 동시 발생"
        elif is_bearish:
            return True, f"음봉 하락 발생({candle_change*100:.2f}% <= {LONG_BEARISH_CANDLE_PCT*100:.1f}%)"
        elif is_daily_drop:
            return True, f"전일 대비 급락 발생({daily_change*100:.2f}% <= {LONG_DAILY_DROP_PCT*100:.1f}%)"

        return False, "진입 조건 미충족"

    def check_exit_signal(
        self,
        current_price: float,
        indicators: Dict[str, Any],
        cycle_info: Dict[str, Any]
    ) -> Tuple[bool, str, float]:
        """
        롱 청산 목표가 산출 및 청산 도달 여부 평가 (포지션 보유 시):
        1. 60일선 상승 중: Target = SMA5 * 1.02 이상 도달 시 전량 청산
        2. 60일선 하락 & 낙폭률 > -30%: Target = SMA5 * 0.99 이하 도달 시 전량 청산 (빠른 본전 탈출)
        3. 60일선 하락 & 낙폭률 <= -30%: Target = SMA5 * 1.03 이상 도달 시 전량 청산 (깊은 하락 후 반등 익절)
        """
        # 포지션 미보유 상태(total_qty == 0 또는 executed_units == 0)인 경우 청산 불필요
        total_qty = cycle_info.get('total_qty', 0.0)
        executed_units = cycle_info.get('executed_units', 0)
        if total_qty <= 0 and executed_units <= 0:
            return False, "롱 포지션 미보유 (청산 조건 패스)", 0.0

        sma5 = indicators.get('sma5', 0.0)
        is_sma60_rising = indicators.get('is_sma60_rising', False)
        drawdown_rate = indicators.get('drawdown_rate', 0.0)

        if is_sma60_rising:
            target_price = sma5 * LONG_EXIT_RISING_SMA
            is_met = current_price >= target_price
            reason = f"60일선 상승 중: SMA5*1.02 목표가(${target_price:,.2f}) 도달 익절"
            return is_met, reason, target_price
        else:
            if drawdown_rate > DRAWDOWN_THRESHOLD:
                # 완만한 하락장: 본전 탈출
                target_price = sma5 * LONG_EXIT_FALLING_SMA_MILD
                is_met = current_price <= target_price
                reason = f"60일선 하락장 & 낙폭률({drawdown_rate*100:.1f}% > -30%): SMA5*0.99 목표가(${target_price:,.2f}) 도달 본전탈출"
                return is_met, reason, target_price
            else:
                # 깊은 하락장: 반등 익절
                target_price = sma5 * LONG_EXIT_FALLING_SMA_DEEP
                is_met = current_price >= target_price
                reason = f"60일선 하락장 & 과낙폭({drawdown_rate*100:.1f}% <= -30%): SMA5*1.03 목표가(${target_price:,.2f}) 도달 반등익절"
                return is_met, reason, target_price
