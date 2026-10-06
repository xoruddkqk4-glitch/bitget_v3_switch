"""
strategy/strategy_b_short.py
B 전략: Short DCA 매매 전략 모듈 (PRD 섹션 3)
상승 조건 발생 시 더미 소진 후 분할 숏 진입 -> 60일선 대칭 조건부 독립 익절
"""

from typing import Dict, Any, Tuple
from strategy.base import BaseStrategy
from config import (
    SHORT_BULLISH_CANDLE_PCT,
    SHORT_DAILY_PUMP_PCT,
    REBOUND_THRESHOLD,
    SHORT_EXIT_RISING_SMA,
    SHORT_EXIT_FALLING_SMA_MILD,
    SHORT_EXIT_FALLING_SMA_DEEP
)


class StrategyBShort(BaseStrategy):
    """
    Short DCA 전략 구현체
    """
    def __init__(self):
        super().__init__(name="strategy_B")

    def check_entry_signal(
        self,
        current_candle: Dict[str, Any],
        prev_candle: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """
        숏 진입 조건 (OR):
        1) 당일 양봉 >= +1.5% (종가 / 시가 - 1 >= +0.015)
        2) 전일 대비 등락률 >= +3.0% (종가 / 전일종가 - 1 >= +0.030)
        """
        candle_change = current_candle.get('candle_change', 0.0)
        daily_change = current_candle.get('daily_change', 0.0)

        is_bullish = candle_change >= SHORT_BULLISH_CANDLE_PCT
        is_daily_pump = daily_change >= SHORT_DAILY_PUMP_PCT

        if is_bullish and is_daily_pump:
            return True, f"양봉 상승({candle_change*100:+.2f}%) 및 전일 대비 급등({daily_change*100:+.2f}%) 동시 발생"
        elif is_bullish:
            return True, f"양봉 상승 발생({candle_change*100:+.2f}% >= {SHORT_BULLISH_CANDLE_PCT*100:+.1f}%)"
        elif is_daily_pump:
            return True, f"전일 대비 급등 발생({daily_change*100:+.2f}% >= {SHORT_DAILY_PUMP_PCT*100:+.1f}%)"

        return False, "진입 조건 미충족"

    def check_exit_signal(
        self,
        current_price: float,
        indicators: Dict[str, Any],
        cycle_info: Dict[str, Any]
    ) -> Tuple[bool, str, float]:
        """
        숏 청산 목표가 산출 및 청산 도달 여부 평가 (포지션 보유 시):
        1. 60일선 상승 중: Target = SMA5 * 0.98 이하 도달 시 전량 청산
        2. 60일선 하락 & 반등폭 < +30%: Target = SMA5 * 1.01 이하 도달 시 전량 청산 (본전 탈출)
        3. 60일선 하락 & 반등폭 >= +30%: Target = SMA5 * 0.97 이하 도달 시 전량 청산 (깊은 반등 후 눌림 익절)
        """
        # 포지션 미보유 상태(total_qty == 0 또는 executed_units == 0)인 경우 청산 불필요
        total_qty = cycle_info.get('total_qty', 0.0)
        executed_units = cycle_info.get('executed_units', 0)
        if total_qty <= 0 and executed_units <= 0:
            return False, "숏 포지션 미보유 (청산 조건 패스)", 0.0

        sma5 = indicators.get('sma5', 0.0)
        is_sma60_rising = indicators.get('is_sma60_rising', False)
        rebound_rate = indicators.get('rebound_rate', 0.0)

        if is_sma60_rising:
            target_price = sma5 * SHORT_EXIT_RISING_SMA
            is_met = current_price <= target_price
            reason = f"60일선 상승 중: SMA5*0.98 목표가(${target_price:,.2f}) 도달 익절"
            return is_met, reason, target_price
        else:
            if rebound_rate < REBOUND_THRESHOLD:
                # 완만한 반등장: 본전 탈출
                target_price = sma5 * SHORT_EXIT_FALLING_SMA_MILD
                is_met = current_price <= target_price
                reason = f"60일선 하락장 & 반등폭({rebound_rate*100:+.1f}% < +30%): SMA5*1.01 목표가(${target_price:,.2f}) 도달 본전탈출"
                return is_met, reason, target_price
            else:
                # 과반등 장세: 깊은 눌림 익절
                target_price = sma5 * SHORT_EXIT_FALLING_SMA_DEEP
                is_met = current_price <= target_price
                reason = f"60일선 하락장 & 과반등({rebound_rate*100:+.1f}% >= +30%): SMA5*0.97 목표가(${target_price:,.2f}) 도달 눌림익절"
                return is_met, reason, target_price
