"""
api/ls_client.py
LS 공통모듈(LS_Common_new / LS_API_Helper_US_new) 기반 미국 현물 일봉 신호 어댑터
- 일봉 OHLCV: LsiUS.GetOhlcv(source="yf_auto") (yfinance → 야후 HTTP → LS → FDR 폴백, 당일 시가 오차 회피)
- 현재가: LsiUS.GetCurrentPrice (LS g3101)
- LS 설정 파일(./myLS_StockInfo.yaml)과 토큰(./ls_real_token.json)은 현재 작업 폴더 기준이므로 봇 루트에서 실행해야 합니다.
"""

import time
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from config import US_SYMBOL, LS_CHANGE_MODE
from api.exceptions import DataUnavailableError
from utils.logger import logger

ET = ZoneInfo("America/New_York")
OHLCV_RETRY_DELAYS = (0.0, 2.0, 4.0)
YF_CHART_URL = "https://query2.finance.yahoo.com/v8/finance/chart/{code}"
YF_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

_LsCommon = None
_LsiUS = None


def _ensure_ls():
    """LS 공통모듈을 최초 사용 시 1회 로드하고 계좌 모드를 설정합니다. (SetChangeMode는 토큰 점검을 수반)"""
    global _LsCommon, _LsiUS
    if _LsiUS is not None:
        return _LsiUS
    try:
        import LS_Common_new as LsCommon
        import LS_API_Helper_US_new as LsiUS
    except Exception as e:
        raise DataUnavailableError(f"[LS] 공통모듈 로드 실패 (pyyaml/pytz 설치 및 봇 루트 실행 확인): {e}") from e
    try:
        LsCommon.SetChangeMode(LS_CHANGE_MODE)
    except Exception as e:
        logger.warning(f"[LS] SetChangeMode({LS_CHANGE_MODE}) 경고: {e}")
    _LsCommon, _LsiUS = LsCommon, LsiUS
    return _LsiUS


def _validate_ohlcv(df: Optional[pd.DataFrame]) -> str:
    """유효하면 빈 문자열, 아니면 사유를 반환합니다."""
    if df is None or len(df) == 0:
        return "빈 DataFrame"
    for col in ("open", "high", "low", "close"):
        if col not in df.columns:
            return f"'{col}' 컬럼 없음"
    last_open = df["open"].iloc[-1]
    if pd.isna(last_open) or float(last_open) <= 0:
        return f"마지막 시가 비정상({last_open})"
    last_close = df["close"].iloc[-1]
    if pd.isna(last_close) or float(last_close) <= 0:
        return f"마지막 종가 비정상({last_close})"
    return ""


def _fetch_today_bar_from_yahoo(today: str) -> Optional[dict]:
    """
    야후 chart 원본에서 오늘(ET) 봉을 읽습니다.
    장중·장마감 직후 야후는 오늘 봉의 close를 비워 보내고(None), 공통모듈 야후 HTTP 경로는 close 결측 행을 버립니다.
    이때 오늘 봉의 시가/고가/저가는 원본 값을, 종가는 meta.regularMarketPrice를 씁니다. (휴장일이면 오늘 행이 없어 None)
    """
    try:
        r = requests.get(YF_CHART_URL.format(code=US_SYMBOL), headers=YF_HEADERS, timeout=10,
                         params={"range": "5d", "interval": "1d"})
        r.raise_for_status()
        res = r.json()["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        dates = pd.to_datetime(res["timestamp"], unit="s", utc=True).tz_convert(ET).strftime("%Y-%m-%d")
        rows = [i for i, d in enumerate(dates) if d == today]
        if not rows:
            return None
        i = rows[-1]
        open_px = q["open"][i]
        if open_px is None or float(open_px) <= 0:
            return None
        meta = res.get("meta") or {}
        meta_dt = pd.to_datetime(meta.get("regularMarketTime") or 0, unit="s", utc=True).tz_convert(ET).strftime("%Y-%m-%d")
        close_px = q["close"][i] or (meta.get("regularMarketPrice") if meta_dt == today else None)
        if close_px is None or float(close_px) <= 0:
            return None
        open_px, close_px = float(open_px), float(close_px)
        return {
            "open": open_px,
            "high": float(q["high"][i] or max(open_px, close_px)),
            "low": float(q["low"][i] or min(open_px, close_px)),
            "close": close_px,
            "volume": float(q["volume"][i] or 0.0),
            "datetime": today,
        }
    except Exception as e:
        logger.warning(f"[LS] 야후 원본 오늘 봉 조회 실패: {e}")
        return None


def fetch_daily_ohlcv(limit: int) -> pd.DataFrame:
    """
    미국 현물 일봉 OHLCV를 반환합니다. (최대 3회 재시도, 실패 시 DataUnavailableError)
    반환 컬럼: open/high/low/close/volume + datetime("YYYY-MM-DD", ET 거래일) + timestamp(ms)
    """
    lsi = _ensure_ls()
    last_reason = ""
    for attempt, delay in enumerate(OHLCV_RETRY_DELAYS, start=1):
        if delay:
            time.sleep(delay)
        try:
            raw = lsi.GetOhlcv("US", US_SYMBOL, limit, source="yf_auto")
        except Exception as e:
            last_reason = f"예외: {e}"
            logger.warning(f"[LS] {US_SYMBOL} 일봉 조회 실패 ({attempt}/{len(OHLCV_RETRY_DELAYS)}): {last_reason}")
            continue
        last_reason = _validate_ohlcv(raw)
        if last_reason:
            logger.warning(f"[LS] {US_SYMBOL} 일봉 데이터 비정상 ({attempt}/{len(OHLCV_RETRY_DELAYS)}): {last_reason}")
            continue

        df = raw[["open", "high", "low", "close", "volume"]].astype(float).copy()
        df["datetime"] = pd.to_datetime(pd.Index(raw.index).astype(str)).strftime("%Y-%m-%d")
        df = df.reset_index(drop=True)

        et_now = datetime.now(ET)
        today = et_now.strftime("%Y-%m-%d")
        if df["datetime"].iloc[-1] < today and et_now.weekday() < 5:
            today_bar = _fetch_today_bar_from_yahoo(today)
            if today_bar:
                df = pd.concat([df, pd.DataFrame([today_bar])], ignore_index=True)
                logger.info(
                    f"[LS] {US_SYMBOL} 오늘 봉({today}) 누락 → 야후 원본으로 보완 "
                    f"(시가 ${today_bar['open']:,.2f}, 종가 ${today_bar['close']:,.2f})"
                )

        dates = pd.to_datetime(df["datetime"])
        df["timestamp"] = ((dates - pd.Timestamp("1970-01-01")) // pd.Timedelta(milliseconds=1)).astype("int64")
        logger.info(f"[LS] {US_SYMBOL} 현물 일봉 {len(df)}개 수집 완료 (마지막 봉(ET): {df['datetime'].iloc[-1]})")
        return df

    raise DataUnavailableError(f"[LS] {US_SYMBOL} 현물 일봉 수집 실패: {last_reason}")


def fetch_current_price() -> Optional[float]:
    """미국 현물 현재가 (LS g3101). 실패 시 None."""
    try:
        price = _ensure_ls().GetCurrentPrice(US_SYMBOL)
    except Exception as e:
        logger.warning(f"[LS] {US_SYMBOL} 현재가 조회 실패: {e}")
        return None
    if price is None or float(price) <= 0:
        logger.warning(f"[LS] {US_SYMBOL} 현재가 응답 비정상: {price}")
        return None
    return float(price)


def is_us_market_open() -> bool:
    """
    미국 정규장 개장 여부 (평일 09:30~16:00 ET + LS 휴장 판정).
    LS 조회 자체가 실패하면 로컬 시계 기준으로만 판정합니다 (휴장일은 호출부의 당일 봉 날짜 가드가 차단).
    """
    try:
        return bool(_ensure_ls().IsMarketOpen(check_holiday=True))
    except Exception as e:
        now = datetime.now(ET)
        minutes = now.hour * 60 + now.minute
        fallback = now.weekday() < 5 and (9 * 60 + 30) <= minutes <= 16 * 60
        logger.warning(f"[LS] 미국장 개장 판정 실패 → 로컬 시계 기준 {fallback} 사용: {e}")
        return fallback
