"""
strategy/indicator.py
5일선, 60일선 방향, 전고점/전저점, 낙폭률/반등폭 산출 지표 계산 모듈
"""

import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional
import pandas as pd
from config import SMA_4H_PERIOD, TIMEFRAME_4H
from utils.logger import logger

KST = timezone(timedelta(hours=9))
FOUR_HOURS_MS = 4 * 60 * 60 * 1000


def _timeframe_to_ms(timeframe: str) -> int:
    """타임프레임 문자열('1h', '4h', '15m', '1d' 등)을 밀리초(ms) 단위로 변환합니다."""
    tf = str(timeframe or "1h").strip().lower()
    try:
        if tf.endswith("m"):
            return int(float(tf[:-1]) * 60 * 1000)
        if tf.endswith("h"):
            return int(float(tf[:-1]) * 60 * 60 * 1000)
        if tf.endswith("d"):
            return int(float(tf[:-1]) * 24 * 60 * 60 * 1000)
    except ValueError:
        pass
    return 60 * 60 * 1000  # 기본 1시간(1h)


class IndicatorCalculator:
    """
    일봉 OHLCV 데이터를 기반으로 전략 판단에 필요한 지표들을 산출합니다.
    """

    @staticmethod
    def calculate(
        df: pd.DataFrame,
        cycle_peak: float = 0.0,
        cycle_trough: float = 0.0,
        mode: str = "bitget_closed",
        live_price: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        OHLCV DataFrame을 분석하여 지표 딕셔너리를 반환합니다.
        - mode="bitget_closed": Bitget 1D 캔들, 09:00 KST 마감된 전일 완성봉(iloc[-2]) 판정 (롤백용)
        - mode="us_intraday_close": 미국 현물 일봉, 장마감 직전 당일 봉(iloc[-1]) 판정.
          live_price가 있으면 마지막 종가를 치환한 뒤 SMA를 계산합니다.
        """
        if df.empty or len(df) < 61:
            raise ValueError(f"지표 산출을 위해서는 최소 61개 이상의 일봉 데이터가 필요합니다. (현재: {len(df)}개)")

        is_us = mode == "us_intraday_close"
        close_series = df['close'].astype(float).copy()
        high_series = df['high'].astype(float).copy()
        low_series = df['low'].astype(float).copy()
        if is_us and live_price is not None and live_price > 0:
            close_series.iloc[-1] = float(live_price)
            high_series.iloc[-1] = max(float(high_series.iloc[-1]), float(live_price))
            low_series.iloc[-1] = min(float(low_series.iloc[-1]), float(live_price))

        # 이동평균선 산출
        sma5_series = close_series.rolling(window=5).mean()
        sma60_series = close_series.rolling(window=60).mean()

        # 미국 현물: 판정 봉 = 장마감 직전 당일 봉 (iloc[-1]), 비교 기준 = 전일 완성봉 (iloc[-2])
        # Bitget: 판정 봉 = 방금 09:00 KST 마감된 전일 완성봉 (iloc[-2]), 비교 기준 = 전전일 완성봉 (iloc[-3])
        eval_idx = -1 if is_us else -2

        # 실시간 가격 (마지막 봉 iloc[-1]: 신호 판정 및 실시간 평가용)
        current_close = float(close_series.iloc[-1])
        current_open = float(df['open'].iloc[-1])

        eval_close = float(close_series.iloc[eval_idx])
        eval_open = float(df['open'].iloc[eval_idx])
        prev_eval_close = float(close_series.iloc[eval_idx - 1])

        # 판정 봉 기준 이동평균선
        sma5_eval = float(sma5_series.iloc[eval_idx])
        sma60_eval = float(sma60_series.iloc[eval_idx])
        sma60_prev_eval = float(sma60_series.iloc[eval_idx - 1])

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

        # 평가 대상 봉 일시 (Bitget: "YYYY-MM-DD 09:00:00" KST / 미국 현물: "YYYY-MM-DD" ET 거래일)
        eval_candle_date = str(df['datetime'].iloc[eval_idx]) if 'datetime' in df.columns else ""

        indicators = {
            'current_price': current_close,
            'signal_price': current_close,
            'exec_price': current_close,
            'signal_source': "LS" if is_us else "bitget",
            'eval_mode': mode,
            'high40': float(high_series.iloc[-40:].max()),
            'low40': float(low_series.iloc[-40:].min()),
            'current_open': current_open,
            'prev_close': eval_close,
            'eval_close': eval_close,
            'eval_open': eval_open,
            'prev_eval_close': prev_eval_close,
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

        candle_label = "기준봉(당일 장마감 직전)" if is_us else "기준완성봉"
        logger.info(
            f"[Indicators] {candle_label}({eval_candle_date}): 종가 ${eval_close:,.2f}, 캔들등락: {candle_change*100:+.2f}%, 전일비: {daily_change*100:+.2f}% | "
            f"실시간가: ${current_close:,.2f} | SMA5: ${sma5_eval:,.2f}, SMA60: ${sma60_eval:,.2f} "
            f"({'상승중' if is_sma60_rising else '하락중'}), 낙폭률: {drawdown_rate*100:+.2f}%, 반등폭: {rebound_rate*100:+.2f}%"
        )
        return indicators

    @staticmethod
    def calculate_4h(
        df_4h: pd.DataFrame,
        now_ms: Optional[int] = None,
        timeframe: str = TIMEFRAME_4H
    ) -> Dict[str, Any]:
        """
        2단계 트레일링용 인트라데이(기본 1시간봉 1h, 또는 4h) OHLCV 데이터를 분석하여 매도 판단 지표를 반환합니다.
        - 실시간 지표(realtime 모드): 진행 중 캔들(iloc[-1]) 포함 SMA5 vs 현재가 (기존 방식)
        - 완성봉 지표(closed_candle 모드): 마감 완료된 캔들만으로 SMA5 vs 최근 완성봉 종가
          (완성 판정: 캔들 시작 timestamp + 타임프레임 길이 <= now_ms)
        """
        min_len = SMA_4H_PERIOD + 1
        tf_label = str(timeframe or TIMEFRAME_4H).upper()
        if df_4h.empty or len(df_4h) < min_len:
            raise ValueError(f"{tf_label} 지표 산출을 위해서는 최소 {min_len}개 이상의 캔들이 필요합니다. (현재: {len(df_4h)}개)")

        now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
        candle_ms = _timeframe_to_ms(timeframe)
        close_series = df_4h['close'].astype(float)
        sma5_4h = float(close_series.rolling(window=SMA_4H_PERIOD).mean().iloc[-1])
        current_price = float(close_series.iloc[-1])
        prev_close = float(close_series.iloc[-2])

        # [실시간] 5MA 대비 위치 (진행 중 캔들 포함)
        is_below_sma5_4h = current_price < sma5_4h  # 롱 꺾임 (하향 이탈)
        is_above_sma5_4h = current_price > sma5_4h  # 숏 꺾임 (상향 돌파)

        # [완성봉] 마감 완료된 캔들만 사용
        if 'timestamp' in df_4h.columns:
            close_ts_series = df_4h['timestamp'].astype('int64') + candle_ms
        else:
            # timestamp 컬럼이 없으면 마지막 캔들을 진행 중으로 간주
            close_ts_series = pd.Series([0] * (len(df_4h) - 1) + [now_ms + 1], index=df_4h.index)
        closed_mask = close_ts_series <= now_ms
        closed_closes = close_series[closed_mask]

        if len(closed_closes) >= SMA_4H_PERIOD:
            closed_close = float(closed_closes.iloc[-1])
            closed_sma5_4h = float(closed_closes.iloc[-SMA_4H_PERIOD:].mean())
            closed_candle_close_ts = int(close_ts_series[closed_mask].iloc[-1])
            closed_candle_close_dt = (
                datetime.fromtimestamp(closed_candle_close_ts / 1000, tz=KST).strftime('%Y-%m-%d %H:%M')
                if closed_candle_close_ts > 0 else ""
            )
            is_closed_below_sma5_4h = closed_close < closed_sma5_4h
            is_closed_above_sma5_4h = closed_close > closed_sma5_4h
        else:
            closed_close = 0.0
            closed_sma5_4h = 0.0
            closed_candle_close_ts = 0
            closed_candle_close_dt = ""
            is_closed_below_sma5_4h = False
            is_closed_above_sma5_4h = False

        indicators_4h = {
            'timeframe': str(timeframe or TIMEFRAME_4H).lower(),
            'current_price': current_price,
            'sma5_4h': sma5_4h,
            'prev_close': prev_close,
            'is_below_sma5_4h': is_below_sma5_4h,
            'is_above_sma5_4h': is_above_sma5_4h,
            'closed_close': closed_close,
            'closed_sma5_4h': closed_sma5_4h,
            'eval_close': closed_close,
            'sma5': closed_sma5_4h,
            'closed_candle_close_ts': closed_candle_close_ts,
            'closed_candle_close_dt': closed_candle_close_dt,
            'is_closed_below_sma5_4h': is_closed_below_sma5_4h,
            'is_closed_above_sma5_4h': is_closed_above_sma5_4h,
            'last_candle_date': str(df_4h['datetime'].iloc[-1]) if 'datetime' in df_4h.columns else ""
        }

        logger.info(
            f"[Indicators {tf_label}] 현재가: ${current_price:,.2f}, 실시간 SMA5: ${sma5_4h:,.2f} | "
            f"완성봉({closed_candle_close_dt} 마감) 종가: ${closed_close:,.2f}, 완성봉 SMA5: ${closed_sma5_4h:,.2f} | "
            f"실시간 하향/상향: {is_below_sma5_4h}/{is_above_sma5_4h}, 완성봉 하향/상향: {is_closed_below_sma5_4h}/{is_closed_above_sma5_4h}"
        )
        return indicators_4h
