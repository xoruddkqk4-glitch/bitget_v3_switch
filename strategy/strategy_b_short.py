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

    def check_stage1_exit_signal(
        self,
        current_price: float,
        indicators: Dict[str, Any],
        cycle_info: Dict[str, Any]
    ) -> Tuple[bool, str, str, float]:
        """
        숏 1차 청산 신호 평가 (일봉 마감 시점 09:10~09:15):
        Returns:
            Tuple[is_met, action_type, reason, target_price]
            - action_type: 'ENTER_STAGE2' (상승눌림/과반등 익절), 'IMMEDIATE_EXIT' (완만한 반등 본탈), 'NONE'
        """
        total_qty = cycle_info.get('total_qty', 0.0)
        executed_units = cycle_info.get('executed_units', 0)
        if total_qty <= 0 and executed_units <= 0:
            return False, "NONE", "숏 포지션 미보유 (청산 조건 패스)", 0.0

        sma5 = indicators.get('sma5', 0.0)
        is_sma60_rising = indicators.get('is_sma60_rising', False)
        rebound_rate = indicators.get('rebound_rate', 0.0)

        if is_sma60_rising:
            target_price = sma5 * SHORT_EXIT_RISING_SMA
            is_met = current_price <= target_price
            action = "ENTER_STAGE2" if is_met else "NONE"
            reason = f"60일선 상승 중: 일봉 SMA5*0.98 목표가(${target_price:,.2f}) 도달 (2단계 4H 5MA 감시 돌입)"
            return is_met, action, reason, target_price
        else:
            if rebound_rate < REBOUND_THRESHOLD:
                # 완만한 반등장: 본전 탈출 (2단계 없이 즉시 청산)
                target_price = sma5 * SHORT_EXIT_FALLING_SMA_MILD
                is_met = current_price <= target_price
                action = "IMMEDIATE_EXIT" if is_met else "NONE"
                reason = f"60일선 하락장 & 반등폭({rebound_rate*100:+.1f}% < +30%): SMA5*1.01 목표가(${target_price:,.2f}) 도달 즉시 본탈"
                return is_met, action, reason, target_price
            else:
                # 과반등 장세: 깊은 눌림 익절 (2단계 감시)
                target_price = sma5 * SHORT_EXIT_FALLING_SMA_DEEP
                is_met = current_price <= target_price
                action = "ENTER_STAGE2" if is_met else "NONE"
                reason = f"60일선 하락장 & 과반등({rebound_rate*100:+.1f}% >= +30%): SMA5*0.97 목표가(${target_price:,.2f}) 도달 (2단계 눌림 감시 돌입)"
                return is_met, action, reason, target_price

    def check_stage2_exit_signal(
        self,
        current_price: float,
        indicators_4h: Dict[str, Any],
        trailing_base_price: float,
        buffer_pct: float = 0.002
    ) -> Tuple[bool, str]:
        """
        숏 2단계 트레일링 매도 평가 (5분 주기 감시):
        1) 4시간봉 5MA 상향 돌파: current_price > sma5_4h
        2) 최소 이익 보존선 상회: current_price >= trailing_base_price * (1.0 + buffer_pct)
        """
        sma5_4h = indicators_4h.get('sma5_4h', 0.0)
        is_above_sma5 = indicators_4h.get('is_above_sma5_4h', current_price > sma5_4h)
        preservation_ceiling = trailing_base_price * (1.0 + buffer_pct) if trailing_base_price > 0 else 0.0

        if is_above_sma5:
            return True, f"4시간봉 5MA(${sma5_4h:,.2f}) 상향 돌파 발생 (하락세 반등 숏 청산)"
        elif preservation_ceiling > 0 and current_price >= preservation_ceiling:
            return True, f"최소 이익 보존선(${preservation_ceiling:,.2f}) 상회 발생 (익절 방어 숏 청산)"

        return False, f"숏 추세 유지 중 (현재가: ${current_price:,.2f}, 4H SMA5: ${sma5_4h:,.2f}, 보존선: ${preservation_ceiling:,.2f})"

    def check_exit_signal(
        self,
        current_price: float,
        indicators: Dict[str, Any],
        cycle_info: Dict[str, Any]
    ) -> Tuple[bool, str, float]:
        """기존 인터페이스 호환용 래퍼 (1차 신호 기준)"""
        is_met, action, reason, target = self.check_stage1_exit_signal(current_price, indicators, cycle_info)
        return is_met, reason, target

