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

        # 실시간 가격 (오늘 미완성 캔들 iloc[-1]: 주문 집행 및 실시간 평가손익용)
        current_close = float(close_series.iloc[-1])
        current_open = float(df['open'].iloc[-1])

        # 신호 판정 기준 일봉: 방금 09:00 마감 완료된 전일 완성봉 (iloc[-2])
        # 비교 기준 전일봉: 전전일 완성봉 (iloc[-3])
        eval_close = float(close_series.iloc[-2])
        eval_open = float(df['open'].iloc[-2])
        prev_eval_close = float(close_series.iloc[-3])

        # 완성봉 기준 이동평균선 (전일 마감 기준 SMA)
        sma5_eval = float(sma5_series.iloc[-2])
        sma60_eval = float(sma60_series.iloc[-2])
        sma60_prev_eval = float(sma60_series.iloc[-3])

        # 60일선 상승 여부 (전일 마감 기준)
        is_sma60_rising = sma60_eval > sma60_prev_eval

        # 캔들 등락률 (전일 완성봉 음봉/양봉: 종가 / 시가 - 1)
        candle_change = (eval_close / eval_open) - 1.0 if eval_open > 0 else 0.0

        # 전일 대비 등락률 (전일 완성봉 종가 / 전전일 종가 - 1)
        daily_change = (eval_close / prev_eval_close) - 1.0 if prev_eval_close > 0 else 0.0

        # 싸이클 전고점/전저점 기준 낙폭률 및 반등폭 산출 (현재가 및 완성봉 종가 반영)
        # Long 전략: 낙폭률 = (현재가 - 전고점) / 전고점
        effective_peak = max(cycle_peak, current_close, eval_close) if cycle_peak > 0 else max(current_close, eval_close)
        drawdown_rate = (current_close - effective_peak) / effective_peak if effective_peak > 0 else 0.0

        # Short 전략: 반등폭 = (현재가 - 전저점) / 전저점
        effective_trough = min(cycle_trough, current_close, eval_close) if cycle_trough > 0 else min(current_close, eval_close)
        rebound_rate = (current_close - effective_trough) / effective_trough if effective_trough > 0 else 0.0

        # 평가 대상 완성봉 일시 (전일 일봉)
        eval_candle_date = str(df['datetime'].iloc[-2]) if 'datetime' in df.columns else ""

        indicators = {
            'current_price': current_close,
            'current_open': current_open,
            'prev_close': eval_close,
            'eval_close': eval_close,
            'eval_open': eval_open,
            'sma5': sma5_eval,
            'sma60': sma60_eval,
            'sma60_prev': sma60_prev_eval,
            'is_sma60_rising': is_sma60_rising,
            'candle_change': candle_change,
            'daily_change': daily_change,
            'effective_peak': effective_peak,
            'effective_trough': effective_trough,
            'drawdown_rate': drawdown_rate,
            'rebound_rate': rebound_rate,
            'candle_date': eval_candle_date,
            'latest_datetime': str(df['datetime'].iloc[-1]) if 'datetime' in df.columns else ""
        }

        logger.info(
            f"[Indicators] 기준완성봉({eval_candle_date}): 종가 ${eval_close:,.2f}, 캔들등락: {candle_change*100:+.2f}%, 전일비: {daily_change*100:+.2f}% | "
            f"실시간가: ${current_close:,.2f} | SMA5: ${sma5_eval:,.2f}, SMA60: ${sma60_eval:,.2f} "
            f"({'상승중' if is_sma60_rising else '하락중'}), 낙폭률: {drawdown_rate*100:+.2f}%, 반등폭: {rebound_rate*100:+.2f}%"
        )
        return indicators

    @staticmethod
    def calculate_4h(df_4h: pd.DataFrame) -> Dict[str, Any]:
        """
        4시간봉 OHLCV 데이터를 분석하여 2단계 트레일링 매도 판단 지표를 반환합니다.
        """
        if df_4h.empty or len(df_4h) < 5:
            raise ValueError(f"4시간봉 지표 산출을 위해서는 최소 5개 이상의 캔들이 필요합니다. (현재: {len(df_4h)}개)")

        close_series = df_4h['close'].astype(float)
        sma5_4h = float(close_series.rolling(window=5).mean().iloc[-1])
        current_price = float(close_series.iloc[-1])
        prev_close = float(close_series.iloc[-2]) if len(close_series) >= 2 else current_price

        # 4H 5MA 대비 위치
        is_below_sma5_4h = current_price < sma5_4h  # 롱 꺾임 (하향 이탈)
        is_above_sma5_4h = current_price > sma5_4h  # 숏 꺾임 (상향 돌파)

        indicators_4h = {
            'current_price': current_price,
            'sma5_4h': sma5_4h,
            'prev_close': prev_close,
            'is_below_sma5_4h': is_below_sma5_4h,
            'is_above_sma5_4h': is_above_sma5_4h,
            'last_candle_date': str(df_4h['datetime'].iloc[-1]) if 'datetime' in df_4h.columns else ""
        }

        logger.info(
            f"[Indicators 4H] 현재가: ${current_price:,.2f}, 4H SMA5: ${sma5_4h:,.2f} | "
            f"롱하향이탈: {is_below_sma5_4h}, 숏상향돌파: {is_above_sma5_4h}"
        )
        return indicators_4h

