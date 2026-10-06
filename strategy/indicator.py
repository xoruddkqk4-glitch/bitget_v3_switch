"""
strategy/indicator.py
5일선, 60일선 방향, 전고점/전저점, 낙폭률/반등폭 산출 지표 계산 모듈
"""

from typing import Dict, Any, Optional
import pandas as pd
from utils.logger import logger


class IndicatorCalculator:
    """
    일봉 OHLCV 데이터를 기반으로 전략 판단에 필요한 지표들을 산출합니다.
    """

    @staticmethod
    def calculate(df: pd.DataFrame, cycle_peak: float = 0.0, cycle_trough: float = 0.0) -> Dict[str, Any]:
        """
        OHLCV DataFrame을 분석하여 지표 딕셔너리를 반환합니다.
        """
        if df.empty or len(df) < 61:
            raise ValueError(f"지표 산출을 위해서는 최소 61개 이상의 일봉 데이터가 필요합니다. (현재: {len(df)}개)")

        # 이동평균선 산출
        close_series = df['close'].astype(float)
        sma5_series = close_series.rolling(window=5).mean()
        sma60_series = close_series.rolling(window=60).mean()

        current_close = float(close_series.iloc[-1])
        current_open = float(df['open'].iloc[-1])
        prev_close = float(close_series.iloc[-2])

        sma5_current = float(sma5_series.iloc[-1])
        sma60_current = float(sma60_series.iloc[-1])
        sma60_prev = float(sma60_series.iloc[-2])

        # 60일선 상승 여부
        is_sma60_rising = sma60_current > sma60_prev

        # 캔들 등락률 (종가 / 시가 - 1)
        candle_change = (current_close / current_open) - 1.0 if current_open > 0 else 0.0

        # 전일 대비 등락률 (종가 / 전일종가 - 1)
        daily_change = (current_close / prev_close) - 1.0 if prev_close > 0 else 0.0

        # 싸이클 전고점/전저점 기준 낙폭률 및 반등폭 산출
        # Long 전략: 낙폭률 = (현재가 - 전고점) / 전고점
        effective_peak = max(cycle_peak, current_close) if cycle_peak > 0 else current_close
        drawdown_rate = (current_close - effective_peak) / effective_peak if effective_peak > 0 else 0.0

        # Short 전략: 반등폭 = (현재가 - 전저점) / 전저점
        effective_trough = min(cycle_trough, current_close) if cycle_trough > 0 else current_close
        rebound_rate = (current_close - effective_trough) / effective_trough if effective_trough > 0 else 0.0

        indicators = {
            'current_price': current_close,
            'current_open': current_open,
            'prev_close': prev_close,
            'sma5': sma5_current,
            'sma60': sma60_current,
            'sma60_prev': sma60_prev,
            'is_sma60_rising': is_sma60_rising,
            'candle_change': candle_change,
            'daily_change': daily_change,
            'effective_peak': effective_peak,
            'effective_trough': effective_trough,
            'drawdown_rate': drawdown_rate,
            'rebound_rate': rebound_rate,
            'candle_date': str(df['datetime'].iloc[-1]) if 'datetime' in df.columns else ""
        }

        logger.info(
            f"[Indicators] 현재가: ${current_close:,.2f}, SMA5: ${sma5_current:,.2f}, SMA60: ${sma60_current:,.2f} "
            f"({'상승중' if is_sma60_rising else '하락중'}), 캔들등락: {candle_change*100:+.2f}%, 전일비: {daily_change*100:+.2f}%, "
            f"낙폭률: {drawdown_rate*100:+.2f}%, 반등폭: {rebound_rate*100:+.2f}%"
        )
        return indicators
