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
    LONG_EXIT_FALLING_SMA_DEEP,
    BREAKEVEN_GUARD_ENABLED,
    BREAKEVEN_GUARD_MIN_PCT,
    STAGE2_SIGNAL_MODE
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
            return True, f"음봉 하락({candle_change*100:+.2f}% <= {LONG_BEARISH_CANDLE_PCT*100:.1f}%) 및 전일비 급락({daily_change*100:+.2f}% <= {LONG_DAILY_DROP_PCT*100:.1f}%) 동시 충족"
        elif is_bearish:
            return True, f"음봉 하락 충족({candle_change*100:+.2f}% <= {LONG_BEARISH_CANDLE_PCT*100:.1f}%, 전일비: {daily_change*100:+.2f}%)"
        elif is_daily_drop:
            return True, f"전일비 급락 충족({daily_change*100:+.2f}% <= {LONG_DAILY_DROP_PCT*100:.1f}%, 캔들: {candle_change*100:+.2f}%)"

        return False, f"음봉 하락 미달({candle_change*100:+.2f}% > {LONG_BEARISH_CANDLE_PCT*100:.1f}%) & 전일비 급락 미달({daily_change*100:+.2f}% > {LONG_DAILY_DROP_PCT*100:.1f}%)"

    def check_stage1_exit_signal(
        self,
        current_price: float,
        indicators: Dict[str, Any],
        cycle_info: Dict[str, Any]
    ) -> Tuple[bool, str, str, float]:
        """
        롱 1차 청산 신호 평가 (일봉 마감 시점 09:10~09:15):
        Returns:
            Tuple[is_met, action_type, reason, target_price]
            - action_type: 'ENTER_STAGE2' (상승장/과낙폭 익절), 'IMMEDIATE_EXIT' (완만한 하락 본탈), 'NONE'
            - 완만한 하락 본탈: 현재가가 SMA5*0.99 이상으로 '회복'했을 때 즉시 청산 (반등 시 탈출)
        """
        total_qty = cycle_info.get('total_qty', 0.0)
        executed_units = cycle_info.get('executed_units', 0)
        if total_qty <= 0 and executed_units <= 0:
            return False, "NONE", "롱 포지션 미보유 (청산 조건 패스)", 0.0

        sma5 = indicators.get('sma5', 0.0)
        is_sma60_rising = indicators.get('is_sma60_rising', False)
        drawdown_rate = indicators.get('drawdown_rate', 0.0)

        if is_sma60_rising:
            target_price = sma5 * LONG_EXIT_RISING_SMA
            is_met = current_price >= target_price
            action = "ENTER_STAGE2" if is_met else "NONE"
            reason = f"60일선 상승 중: 일봉 SMA5*1.02 목표가(${target_price:,.2f}) 도달 (2단계 4H 5MA 감시 돌입)"
            return is_met, action, reason, target_price
        else:
            if drawdown_rate > DRAWDOWN_THRESHOLD:
                # 완만한 하락장: 본전 탈출 (2단계 없이 즉시 청산)
                target_price = sma5 * LONG_EXIT_FALLING_SMA_MILD
                is_met = current_price >= target_price
                action = "IMMEDIATE_EXIT" if is_met else "NONE"
                reason = f"60일선 하락장 & 낙폭률({drawdown_rate*100:.1f}% > -30%): 현재가 ≥ SMA5*0.99 목표가(${target_price:,.2f}) 회복 → 즉시 본탈"
                if is_met and BREAKEVEN_GUARD_ENABLED:
                    avg_price = float(cycle_info.get('avg_price', 0.0) or 0.0)
                    guard_price = avg_price * (1.0 + BREAKEVEN_GUARD_MIN_PCT)
                    if avg_price > 0 and current_price < guard_price:
                        return False, "NONE", (
                            f"{reason} | 본전 가드 미달(현재가 ${current_price:,.2f} < 평단×{1.0 + BREAKEVEN_GUARD_MIN_PCT:.3f} ${guard_price:,.2f}) → 보유 유지"
                        ), target_price
                return is_met, action, reason, target_price
            else:
                # 깊은 하락장: 반등 익절 (2단계 감시)
                target_price = sma5 * LONG_EXIT_FALLING_SMA_DEEP
                is_met = current_price >= target_price
                action = "ENTER_STAGE2" if is_met else "NONE"
                reason = f"60일선 하락장 & 과낙폭({drawdown_rate*100:.1f}% <= -30%): SMA5*1.03 목표가(${target_price:,.2f}) 도달 (2단계 반등 감시 돌입)"
                return is_met, action, reason, target_price

    def check_stage2_exit_signal(
        self,
        current_price: float,
        indicators_4h: Dict[str, Any],
        trailing_base_price: float,
        buffer_pct: float = 0.002,
        trailing_activated_ts: int = 0,
        signal_mode: str = STAGE2_SIGNAL_MODE
    ) -> Tuple[bool, str]:
        """
        롱 2단계 트레일링 매도 평가 (5분 주기 감시):
        1) 최소 이익 보존선 하회 (실시간가, 매 5분): current_price <= trailing_base_price * (1.0 - buffer_pct)
        2) 4시간봉 5MA 하향 이탈:
           - closed_candle(기본): 트레일링 돌입 이후 마감된 4H 완성봉 종가 < 완성봉 SMA5
           - realtime: current_price < 진행 중 캔들 포함 SMA5 (기존 방식)
        """
        preservation_floor = trailing_base_price * (1.0 - buffer_pct) if trailing_base_price > 0 else 0.0
        if preservation_floor > 0 and current_price <= preservation_floor:
            return True, f"최소 이익 보존선(${preservation_floor:,.2f}) 하회 발생 (익절 방어 청산)"

        if signal_mode == "realtime":
            sma5_4h = indicators_4h.get('sma5_4h', 0.0)
            if indicators_4h.get('is_below_sma5_4h', current_price < sma5_4h):
                return True, f"4시간봉 5MA(${sma5_4h:,.2f}) 하향 이탈 발생 (실시간 기준, 추세 꺾임 익절)"
            return False, f"추세 유지 중 [실시간] (현재가: ${current_price:,.2f}, 4H SMA5: ${sma5_4h:,.2f}, 보존선: ${preservation_floor:,.2f})"

        closed_close = float(indicators_4h.get('closed_close', 0.0) or 0.0)
        closed_sma5 = float(indicators_4h.get('closed_sma5_4h', 0.0) or 0.0)
        closed_ts = int(indicators_4h.get('closed_candle_close_ts', 0) or 0)
        closed_dt = indicators_4h.get('closed_candle_close_dt', '')
        is_fresh = closed_ts > int(trailing_activated_ts or 0)
        if closed_sma5 > 0 and is_fresh and indicators_4h.get('is_closed_below_sma5_4h', False):
            return True, f"4시간봉 완성봉({closed_dt} 마감) 종가 ${closed_close:,.2f} < 5MA ${closed_sma5:,.2f} 하향 이탈 (추세 꺾임 익절)"

        wait_txt = "" if is_fresh else " | 트레일링 돌입 이후 마감된 4H봉 대기 중"
        return False, (
            f"추세 유지 중 [완성봉] (현재가: ${current_price:,.2f}, 완성봉 종가: ${closed_close:,.2f}, "
            f"완성봉 SMA5: ${closed_sma5:,.2f}, 보존선: ${preservation_floor:,.2f}{wait_txt})"
        )

    def check_exit_signal(
        self,
        current_price: float,
        indicators: Dict[str, Any],
        cycle_info: Dict[str, Any]
    ) -> Tuple[bool, str, float]:
        """기존 인터페이스 호환용 래퍼 (1차 신호 기준)"""
        is_met, action, reason, target = self.check_stage1_exit_signal(current_price, indicators, cycle_info)
        return is_met, reason, target

