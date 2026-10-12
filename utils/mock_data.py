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
