"""
main.py
bitget_v3_switch 2단계 스마트 트레일링 자동매매 시스템 메인 엔트리 포인트
- 일봉 신호: 미국 SOXL 현물 일봉(LS 공통모듈, yf_auto) / 주문·포지션·2단계 감시: Bitget 선물
- 일봉 마감 평가: 미국 정규장 마감 직전 15:54 ~ 15:59 ET 사이 1회 실행 (Crontab 3-59/5 기준 15:58 틱, */5 기준 15:55 틱)
  (DAILY_SIGNAL_SOURCE=bitget 롤백 시: 기존 Bitget 1D 캔들, 09:10 ~ 09:30 KST)
- 1시간봉 2단계 트레일링 감시: 5분 주기(Crontab 또는 백그라운드 루프) 실행
- 실전 모드(PAPER_TRADING=false)에서는 모의 데이터/모의 잔고로 절대 매매하지 않습니다.
"""

import argparse
import math
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from config import (
    SYMBOL,
    TIMEFRAME,
    CANDLE_LIMIT,
    TIMEFRAME_4H,
    CANDLE_LIMIT_4H,
    DAILY_SIGNAL_SOURCE,
    US_SYMBOL,
    LS_OHLCV_LIMIT,
    DAILY_EVAL_ET_HOUR,
    DAILY_EVAL_ET_START_MIN,
    DAILY_EVAL_ET_END_MIN,
    DAILY_EVAL_HOUR_KST,
    DAILY_EVAL_START_MIN,
    DAILY_EVAL_END_MIN,
    WEEKEND_FREEZE_ENABLED,
    WEEKEND_FREEZE_DAYS,
    PAPER_TRADING,
    UNIT_DIVISOR,
    MIN_ORDER_USD,
    ALERT_COOLDOWN_SEC,
    STAGE1_PARTIAL_EXIT_PCT,
    HYBRID_TRAILING_MIN_PNL_PCT,
    DRAWDOWN_THRESHOLD,
    REBOUND_THRESHOLD,
    LONG_EXIT_RISING_SMA,
    LONG_EXIT_FALLING_SMA_MILD,
    LONG_EXIT_FALLING_SMA_DEEP,
    SHORT_EXIT_RISING_SMA,
    SHORT_EXIT_FALLING_SMA_MILD,
    SHORT_EXIT_FALLING_SMA_DEEP,
    validate_live_config,
)
from api import ls_client
from api.exceptions import TradingError, DataUnavailableError, StateCorruptedError
from utils.logger import logger
from utils.notifier import notifier
from utils.excel_logger import excel_logger
from utils.formatters import format_progress_single_track
from utils.mock_data import generate_mock_ohlcv
from utils.alert_throttle import should_send_alert
from api.bitget_client import bitget_client
from strategy.indicator import IndicatorCalculator
from manager.cycle_manager import cycle_manager
from manager.reconciler import reconciler, ReconcileResult
from manager.trader import trader, SIDE_CFG

KST = timezone(timedelta(hours=9))
ET = ZoneInfo("America/New_York")
USE_LS = DAILY_SIGNAL_SOURCE == "ls"
EVAL_WINDOW_LABEL = (
    f"{DAILY_EVAL_ET_HOUR}:{DAILY_EVAL_ET_START_MIN:02d}~{DAILY_EVAL_ET_HOUR}:{DAILY_EVAL_ET_END_MIN:02d} ET" if USE_LS
    else f"{DAILY_EVAL_HOUR_KST:02d}:{DAILY_EVAL_START_MIN:02d}~{DAILY_EVAL_HOUR_KST:02d}:{DAILY_EVAL_END_MIN:02d} KST"
)
MIN_DAILY_CANDLES = 61   # 일봉 지표 산출 최소 캔들 수
MIN_4H_CANDLES = 6       # 4시간봉 지표 산출 최소 캔들 수 (완성봉 SMA5 = 완성봉 5개 + 진행 중 1개)


def escape_tg(text: str) -> str:
    """텔레그램 HTML 파싱 오류 방지를 위한 특수문자 이스케이프"""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send_live_halt_alert(stage: str, reason: str):
    """실전 매매 중단 경보 (설정/데이터/상태 오류)"""
    kst_err = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')
    notifier.send_message(
        f"<b>🚨 [Bitget V3 Switch LIVE 매매 중단]</b>\n"
        f"• <b>발생 일시:</b> {kst_err}\n"
        f"• <b>단계:</b> {escape_tg(stage)}\n"
        f"• <b>사유:</b> {escape_tg(reason)}\n"
        f"• <b>조치:</b> logs/trading.log 확인 후 원인 해결"
    )


def load_ohlcv_or_abort(timeframe: str, limit: int, min_len: int) -> pd.DataFrame:
    """
    OHLCV를 수집합니다.
    - 실전: 수집 실패/캔들 부족 시 DataUnavailableError (모의 데이터 대체 금지)
    - 모의: 수집 실패/캔들 부족 시 모의 데이터로 대체
    """
    try:
        df = bitget_client.fetch_ohlcv(symbol=SYMBOL, timeframe=timeframe, limit=limit)
    except Exception as e:
        if PAPER_TRADING:
            logger.warning(f"[PAPER] {timeframe} 캔들 수집 실패 ({e}) → 모의 데이터로 진행합니다.")
            return generate_mock_ohlcv(limit, timeframe=timeframe)
        raise DataUnavailableError(f"[LIVE] {timeframe} 캔들 수집 실패: {e}") from e

    if df.empty or len(df) < min_len:
        if PAPER_TRADING:
            logger.warning(f"[PAPER] {timeframe} 캔들 부족({len(df)} < {min_len}) → 모의 데이터로 진행합니다.")
            return generate_mock_ohlcv(limit, timeframe=timeframe)
        raise DataUnavailableError(f"[LIVE] {timeframe} 캔들 부족 ({len(df)} < {min_len})")
    return df


def load_us_daily_or_abort() -> pd.DataFrame:
    """
    미국 현물 일봉 OHLCV를 수집합니다.
    - 실전: 수집 실패/캔들 부족 시 DataUnavailableError (Bitget 자동 대체·모의 데이터 대체 금지)
    - 모의: 수집 실패/캔들 부족 시 마지막 봉이 오늘(ET)인 모의 데이터로 대체
    """
    try:
        df = ls_client.fetch_daily_ohlcv(LS_OHLCV_LIMIT)
        if len(df) >= MIN_DAILY_CANDLES:
            return df
        reason = f"{US_SYMBOL} 현물 일봉 캔들 부족 ({len(df)} < {MIN_DAILY_CANDLES})"
    except DataUnavailableError as e:
        reason = str(e)

    if not PAPER_TRADING:
        raise DataUnavailableError(f"[LIVE] {reason}")
    logger.warning(f"[PAPER] {reason} → 모의 데이터로 진행합니다.")
    df = generate_mock_ohlcv(LS_OHLCV_LIMIT, timeframe=TIMEFRAME)
    today = datetime.now(ET).date()
    df['datetime'] = [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in reversed(range(len(df)))]
    return df


def is_daily_evaluation_window() -> bool:
    """일봉 평가 시간대 여부 (ls: 평일 15:54~15:59 ET / bitget 롤백: 09:10~09:30 KST)"""
    if USE_LS:
        et_now = datetime.now(ET)
        return (
            et_now.weekday() < 5 and
            et_now.hour == DAILY_EVAL_ET_HOUR and
            DAILY_EVAL_ET_START_MIN <= et_now.minute <= DAILY_EVAL_ET_END_MIN
        )
    kst_now = datetime.now(KST)
    return (
        kst_now.hour == DAILY_EVAL_HOUR_KST and
        DAILY_EVAL_START_MIN <= kst_now.minute <= DAILY_EVAL_END_MIN
    )


def _num(value: Any, digits: int = 4) -> Optional[float]:
    """JSON 직렬화용 숫자 정리 (NaN/None → None)"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else round(v, digits)


def build_daily_indicators_snapshot(indicators: Dict[str, Any]) -> Dict[str, Any]:
    """대시보드용 일봉 평가 스냅샷 (숫자 필드만, 청산 배수는 전략 1차 청산 로직과 동일한 분기)"""
    rising = bool(indicators.get('is_sma60_rising'))
    dd = float(indicators.get('drawdown_rate', 0.0) or 0.0)
    rb = float(indicators.get('rebound_rate', 0.0) or 0.0)
    sma5 = float(indicators.get('sma5', 0.0) or 0.0)
    partial = f"{STAGE1_PARTIAL_EXIT_PCT:g}% 선익절 + 2단계 승격"

    def pct(mult: float) -> str:
        return f"{(mult - 1.0) * 100:+.1f}%"

    if rising:
        long_mult = LONG_EXIT_RISING_SMA
        long_rule = f"일봉 5MA {pct(long_mult)} 이상 장마감 시 {partial} (60일선 상승)"
    elif dd > DRAWDOWN_THRESHOLD:
        long_mult = LONG_EXIT_FALLING_SMA_MILD
        long_rule = (
            f"일봉 5MA {pct(long_mult)} 이상 회복 시 본전탈출 "
            f"(수익률 +{HYBRID_TRAILING_MIN_PNL_PCT:g}% 이상이면 2단계 승격, 60일선 하락·완만한 낙폭)"
        )
    else:
        long_mult = LONG_EXIT_FALLING_SMA_DEEP
        long_rule = f"일봉 5MA {pct(long_mult)} 이상 장마감 시 {partial} (60일선 하락·과낙폭)"

    if rising:
        short_mult = SHORT_EXIT_RISING_SMA
        short_rule = f"일봉 5MA {pct(short_mult)} 이하 장마감 시 {partial} (60일선 상승)"
    elif rb < REBOUND_THRESHOLD:
        short_mult = SHORT_EXIT_FALLING_SMA_MILD
        short_rule = (
            f"일봉 5MA {pct(short_mult)} 이하 하락 시 본전탈출 "
            f"(수익률 +{HYBRID_TRAILING_MIN_PNL_PCT:g}% 이상이면 2단계 승격, 60일선 하락·완만한 반등)"
        )
    else:
        short_mult = SHORT_EXIT_FALLING_SMA_DEEP
        short_rule = f"일봉 5MA {pct(short_mult)} 이하 장마감 시 {partial} (60일선 하락·과반등)"

    sma60 = _num(indicators.get('sma60'))
    return {
        "source": indicators.get('signal_source', 'bitget'),
        "candle_date": indicators.get('candle_date', ''),
        "evaluated_at_kst": datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S'),
        "eval_open": _num(indicators.get('eval_open')),
        "eval_close": _num(indicators.get('eval_close')),
        "prev_close": _num(indicators.get('prev_eval_close')),
        "signal_price": _num(indicators.get('signal_price')),
        "exec_price": _num(indicators.get('exec_price')),
        "candle_change_pct": _num(float(indicators.get('candle_change', 0.0)) * 100.0, 2),
        "daily_change_pct": _num(float(indicators.get('daily_change', 0.0)) * 100.0, 2),
        "sma5": _num(sma5),
        "sma60": sma60,
        "prev_sma60": _num(indicators.get('sma60_prev')),
        "has_sma60": sma60 is not None and sma60 > 0,
        "is_sma60_rising": rising,
        "high40": _num(indicators.get('high40')),
        "low40": _num(indicators.get('low40')),
        "dd_from_high_pct": _num(dd * 100.0, 2),
        "rebound_from_low_pct": _num(rb * 100.0, 2),
        "long_target_mult": long_mult,
        "short_target_mult": short_mult,
        "long_target_price": _num(sma5 * long_mult),
        "short_target_price": _num(sma5 * short_mult),
        "long_exit_rule": long_rule,
        "short_exit_rule": short_rule,
    }


def sync_4h_trend_state(context_label: str = "Pipeline"):
    """대시보드 STATE_SYNC용 4시간봉 5MA 상승 추세 상태 갱신 (보유 0개여도 동기화)"""
    try:
        df_4h_sync = bitget_client.fetch_ohlcv(symbol=SYMBOL, timeframe=TIMEFRAME_4H, limit=CANDLE_LIMIT_4H)
        if df_4h_sync is not None and not df_4h_sync.empty and len(df_4h_sync) >= MIN_4H_CANDLES:
            ind_4h = IndicatorCalculator.calculate_4h(df_4h_sync)
            c_4h = float(ind_4h.get("closed_close") or ind_4h.get("eval_close") or 0.0)
            m_4h = float(ind_4h.get("closed_sma5_4h") or ind_4h.get("sma5") or 0.0)
            if c_4h > 0 and m_4h > 0:
                cycle_manager.update_trend_riding(is_active=(c_4h >= m_4h), prev_close_4h=c_4h, ma5_4h=m_4h)
    except Exception as e_4h:
        logger.warning(f"[{context_label}] 4시간봉 추세 상태 동기화 생략: {e_4h}")


def format_reconcile_summary(results: Dict[str, ReconcileResult]) -> str:
    """포지션 대조 결과 한 줄 요약"""
    if not results:
        return "대조 미실행"
    parts = []
    for name, r in results.items():
        label = SIDE_CFG[name]["label"]
        if r.status == "MISMATCH":
            parts.append(f"{label} ⛔MISMATCH(state {r.state_qty:,.4f} / 거래소 {r.exchange_qty:,.4f})")
        else:
            parts.append(f"{label} {r.status}")
    return " / ".join(parts)


def run_reconciliation(stage: str, throttle: bool) -> Dict[str, ReconcileResult]:
    """포지션 대조 실행 및 불일치 경보 (throttle=True면 동일 경보 쿨다운 적용)"""
    results = reconciler.reconcile()
    for name, r in results.items():
        if not r.blocked:
            continue
        if throttle and not should_send_alert(f"mismatch_{name}", ALERT_COOLDOWN_SEC):
            continue
        notifier.send_message(
            f"<b>⛔ [Bitget V3 Switch 포지션 불일치]</b>\n"
            f"• <b>단계:</b> {escape_tg(stage)}\n"
            f"• <b>전략:</b> {name} ({SIDE_CFG[name]['label']})\n"
            f"• <b>내용:</b> {escape_tg(r.detail)}\n"
            f"• <b>조치:</b> 해당 전략 매매 차단 중. python main.py --reconcile 로 확인 후 필요 시 --adopt-exchange"
        )
    return results


def run_pipeline(force: bool = False) -> bool:
    """
    일봉 마감 시점 통합 실행 파이프라인 (ls: 15:54~15:59 ET / bitget: 09:10~09:30 KST, 동일 판정 봉은 1일 1회만 평가)
    force=True: 이미 평가한 봉이어도 재평가 (--run-once --force 전용)
    0. 주말/휴장 가드 및 state.json 무결성 확인
    1. 거래소 환경 점검/검증
    2. 일봉 OHLCV 수집 (ls: 미국 SOXL 현물 / bitget: Bitget 1D, 실전 실패 시 중단 - 소스 간 자동 대체 없음)
    3. 거래소 실제 포지션 대조 (불일치 전략 매매 차단)
    4. 지표 산출 → 5. 1차 청산/진입 평가 → 6. 텔레그램 일일 보고서 발송
    """
    start_time = datetime.now()
    logger.info("=" * 70)
    logger.info(f"[Pipeline] 일봉 마감 실행 파이프라인 시작 ({start_time.strftime('%Y-%m-%d %H:%M:%S')}, 신호 소스: {DAILY_SIGNAL_SOURCE})")
    logger.info("=" * 70)

    try:
        # 0. 주말/휴장 가드 (ls: 미국 ET 평일 + 정규장 개장 여부 / bitget: KST 일·월요일 아침 캔들 동결)
        if not force:
            if USE_LS:
                if datetime.now(ET).weekday() >= 5:
                    logger.info("[Pipeline] 미국 주말(ET)로 일봉 평가를 건너뜁니다. (강제 실행: --run-once --force)")
                    return True
                if not ls_client.is_us_market_open():
                    logger.info("[Pipeline] 미국 정규장 휴장 또는 장외 시간(ET)으로 일봉 평가를 건너뜁니다. (강제 실행: --run-once --force)")
                    return True
            else:
                kst_now = datetime.now(KST)
                if WEEKEND_FREEZE_ENABLED and kst_now.weekday() in WEEKEND_FREEZE_DAYS:
                    day_name = "일요일" if kst_now.weekday() == 6 else "월요일"
                    logger.info(f"[Pipeline] 주말 미국 본장 휴장({day_name} 09:10 KST)으로 일봉 마감 평가가 동결(Freeze)되었습니다. (강제 실행: --run-once --force)")
                    return True

        # 0-1. state.json 무결성 확인 (실전 모드 손상 시 중단)
        if cycle_manager.load_error:
            raise StateCorruptedError(cycle_manager.load_error)

        # 1. 거래소 환경 점검 (실전: 헤지모드/마진/레버리지 검증 실패 시 예외)
        bitget_client.setup_exchange()

        # 2. 일봉 OHLCV 수집
        live_price: Optional[float] = None
        if USE_LS:
            df = load_us_daily_or_abort()
            last_bar = str(df['datetime'].iloc[-1])
            et_today = datetime.now(ET).strftime('%Y-%m-%d')
            if last_bar == et_today:
                live_price = ls_client.fetch_current_price()
            elif not force:
                msg = (
                    f"미국 {US_SYMBOL} 현물 일봉의 마지막 봉({last_bar})이 오늘({et_today} ET)이 아닙니다. "
                    f"휴장 또는 데이터 지연으로 일봉 평가를 건너뜁니다."
                )
                logger.warning(f"[Pipeline] {msg}")
                if should_send_alert("ls_daily_bar_stale", ALERT_COOLDOWN_SEC):
                    notifier.send_message(f"<b>⚠️ [Bitget V3 Switch 일봉 평가 생략]</b>\n• {escape_tg(msg)}")
                return True
            else:
                logger.warning(f"[Pipeline] --force: 마지막 봉({last_bar})을 현재가 치환 없이 그대로 판정합니다.")
        else:
            df = load_ohlcv_or_abort(TIMEFRAME, CANDLE_LIMIT, MIN_DAILY_CANDLES)

        # 3. 거래소 실제 포지션 대조
        recon_results = run_reconciliation(stage="일봉 파이프라인", throttle=False)
        blocked = {name for name, r in recon_results.items() if r.blocked}

        # 4. 싸이클 최고/최저가 로드 및 일봉 지표 산출
        state_a = cycle_manager.get_strategy_state("strategy_A")
        state_b = cycle_manager.get_strategy_state("strategy_B")
        indicators = IndicatorCalculator.calculate(
            df=df,
            cycle_peak=state_a.get("cycle_peak", 0.0),
            cycle_trough=state_b.get("cycle_trough", 0.0),
            mode="us_intraday_close" if USE_LS else "bitget_closed",
            live_price=live_price
        )

        # 4-1. 1일 1회 가드: 동일 판정 봉으로 이미 평가했다면 매매 없이 종료 (더미/매수 이중 집행 방지)
        eval_candle = indicators.get('candle_date', '')
        if not force and cycle_manager.is_daily_evaluated(eval_candle):
            logger.info(f"[Pipeline] 이미 평가 완료된 일봉({eval_candle}) - 중복 실행을 건너뜁니다. (재평가: --run-once --force)")
            return True

        # 4-2. 주문 기준가: Bitget 선물 현재가 (신호는 미국 현물, 주문 수량·기준가는 Bitget)
        if USE_LS:
            indicators['exec_price'] = bitget_client.fetch_last_price(fallback=indicators['signal_price'])
            logger.info(
                f"[Pipeline] 신호가(미국 {US_SYMBOL} 현물) ${indicators['signal_price']:,.2f} / "
                f"주문 기준가(Bitget) ${indicators['exec_price']:,.2f}"
            )

        # 5. Trader 주문 집행 및 1차 청산 / 매수 평가
        logs = trader.process_daily_cycle(indicators, blocked=blocked)
        cycle_manager.mark_daily_evaluated(eval_candle)

        # 5-0. 대시보드 STATE_SYNC용 일봉 평가 지표 스냅샷
        try:
            cycle_manager.update_daily_indicators(build_daily_indicators_snapshot(indicators))
        except Exception as e_snap:
            logger.warning(f"[Pipeline] 일봉 지표 스냅샷 갱신 실패: {e_snap}")

        # 5-1. 대시보드 STATE_SYNC용 4시간봉 5MA 상승 추세 상태 갱신 (보유 0개여도 동기화)
        sync_4h_trend_state(context_label="Pipeline")

        # 6. 상태 요약 및 텔레그램 발송
        updated_state_a = cycle_manager.get_strategy_state("strategy_A")
        updated_state_b = cycle_manager.get_strategy_state("strategy_B")

        trailing_info_a = " [2단계 트레일링 감시 중]" if updated_state_a.get("trailing_mode") else ""
        trailing_info_b = " [2단계 트레일링 감시 중]" if updated_state_b.get("trailing_mode") else ""

        total_balance = trader.last_total_balance
        free_margin = trader.last_free_margin
        unit_base_usd = trader.last_unit_base_usd
        hedge_unit_usd = free_margin / UNIT_DIVISOR

        # 평가손익은 Bitget 평단 대비이므로 Bitget 주문 기준가(exec_price)로 계산
        signal_price = indicators.get('signal_price', indicators['current_price'])
        current_price = indicators.get('exec_price') or signal_price
        eval_close = indicators.get('eval_close', signal_price)
        candle_change = indicators.get('candle_change', 0.0) * 100.0
        if USE_LS:
            cd = str(indicators.get('candle_date', ''))
            md = f"{cd[5:7]}/{cd[8:10]}" if len(cd) >= 10 else cd
            candle_basis = f" (미국 {US_SYMBOL} 현물 · {md} ET 장마감 직전)"
            price_line = f"{US_SYMBOL} 현물 ${signal_price:,.2f} / Bitget ${current_price:,.2f}"
        else:
            candle_basis = ""
            price_line = f"${current_price:,.2f}"
        change_icon = "🔻" if candle_change < 0 else "🔺"
        trend_text = "상승" if indicators.get('is_sma60_rising') else "하락"

        # [전략 A - Long] 평가손익 및 실현손익
        eval_a = trader.last_eval_a
        eval_b = trader.last_eval_b

        qty_a = updated_state_a.get('total_qty', 0.0)
        avg_a = updated_state_a.get('avg_price', 0.0)
        if qty_a > 0 and avg_a > 0:
            pnl_usd_a = (current_price - avg_a) * qty_a
            pnl_pct_a = ((current_price / avg_a) - 1.0) * 100.0
            pnl_str_a = f"<b>{pnl_usd_a:+,.2f} USD ({pnl_pct_a:+.2f}%)</b>"
            pos_line_a = f"{qty_a:,.4f} Qty (평단 ${avg_a:,.2f})"
            pos_summary_a = f"롱 {pnl_usd_a:+,.2f} USD ({pnl_pct_a:+.2f}%)"
        elif eval_a.get("cleared"):
            c_pnl_a = eval_a.get("cleared_pnl_usd", 0.0)
            c_pct_a = eval_a.get("cleared_pnl_pct", 0.0)
            c_type_a = eval_a.get("cleared_type", "청산")
            pnl_str_a = "$0.00 USD (0.00%)"
            pos_line_a = f"미보유 (당일 {c_type_a} 청산 완료)"
            pos_summary_a = f"롱 {c_type_a}완료 ({c_pnl_a:+,.2f} USD, {c_pct_a:+.2f}%)"
        else:
            pnl_str_a = "$0.00 USD (0.00%)"
            pos_line_a = "미보유 (0.0000 Qty)"
            pos_summary_a = "롱 미보유"

        cum_pnl_a = updated_state_a.get('cumulative_realized_pnl', 0.0)
        last_pnl_a = updated_state_a.get('last_realized_pnl', 0.0)
        last_pct_a = updated_state_a.get('last_realized_pct', 0.0)
        last_closed_a_str = f" (직전: {last_pnl_a:+,.2f} USD, {last_pct_a:+.2f}%)" if last_pnl_a != 0 else ""

        # [전략 B - Short] 평가손익 및 실현손익
        qty_b = updated_state_b.get('total_qty', 0.0)
        avg_b = updated_state_b.get('avg_price', 0.0)
        if qty_b > 0 and avg_b > 0:
            pnl_usd_b = (avg_b - current_price) * qty_b
            pnl_pct_b = ((avg_b - current_price) / avg_b) * 100.0
            pnl_str_b = f"<b>{pnl_usd_b:+,.2f} USD ({pnl_pct_b:+.2f}%)</b>"
            pos_line_b = f"{qty_b:,.4f} Qty (평단 ${avg_b:,.2f})"
            pos_summary_b = f"숏 {pnl_usd_b:+,.2f} USD ({pnl_pct_b:+.2f}%)"
        elif eval_b.get("cleared"):
            c_pnl_b = eval_b.get("cleared_pnl_usd", 0.0)
            c_pct_b = eval_b.get("cleared_pnl_pct", 0.0)
            c_type_b = eval_b.get("cleared_type", "청산")
            pnl_str_b = "$0.00 USD (0.00%)"
            pos_line_b = f"미보유 (당일 {c_type_b} 청산 완료)"
            pos_summary_b = f"숏 {c_type_b}완료 ({c_pnl_b:+,.2f} USD, {c_pct_b:+.2f}%)"
        else:
            pnl_str_b = "$0.00 USD (0.00%)"
            pos_line_b = "미보유 (0.0000 Qty)"
            pos_summary_b = "숏 미보유"

        cum_pnl_b = updated_state_b.get('cumulative_realized_pnl', 0.0)
        last_pnl_b = updated_state_b.get('last_realized_pnl', 0.0)
        last_pct_b = updated_state_b.get('last_realized_pct', 0.0)
        last_closed_b_str = f" (직전: {last_pnl_b:+,.2f} USD, {last_pct_b:+.2f}%)" if last_pnl_b != 0 else ""

        # 차수 게이지 바 (only_bar=True로 독립 행 분리)
        dummy_a = updated_state_a.get('dummy_count', 0)
        exec_a = updated_state_a.get('executed_units', 0)
        gauge_bar_a = format_progress_single_track(dummy_a, exec_a, only_bar=True)

        dummy_b = updated_state_b.get('dummy_count', 0)
        exec_b = updated_state_b.get('executed_units', 0)
        gauge_bar_b = format_progress_single_track(dummy_b, exec_b, only_bar=True)

        # 1.0 Unit 기본 금액 라벨: 상대편 보유 여부에 따라 단일/헤지 표기
        unit_info_a = f"헤지(롱) ${hedge_unit_usd:,.0f}" if qty_b > 0 else f"단일(롱) ${unit_base_usd:,.0f}"
        unit_info_b = f"헤지(숏) ${hedge_unit_usd:,.0f}" if qty_a > 0 else f"단일(숏) ${unit_base_usd:,.0f}"
        unit_line = f"{unit_info_a} / {unit_info_b}"

        # 당일 전략별 판단 결과 (진입 -> 청산 -> 조치 순서 통일, 당일 청산 시 최우선 강조)
        if eval_a.get("cleared"):
            c_type_a = eval_a.get("cleared_type", "청산")
            c_pct_a = eval_a.get("cleared_pnl_pct", 0.0)
            default_title_a = f"[{c_type_a} 청산 완료] ({c_pct_a:+.2f}%)"
        else:
            default_title_a = "더미 진입 완료" if eval_a.get("status") == "DUMMY_CONSUMED" else ("포지션 유지 (매매 없음)" if qty_a > 0 else "관망 (신호 미발생)")
        action_title_a = escape_tg(eval_a.get("action_title") or default_title_a)
        entry_reason_a = escape_tg(eval_a.get("entry_reason") or eval_a.get("reason") or "진입 조건 정보 없음")
        exit_reason_a = escape_tg(eval_a.get("exit_reason") or ("미보유 (해당 없음)" if qty_a <= 0 else "청산 조건 미충족"))
        action_detail_a = escape_tg(eval_a.get("action_detail") or ("포지션 미보유 유지" if qty_a <= 0 else f"기존 포지션({qty_a:,.4f} Qty) 홀딩 유지"))

        if eval_b.get("cleared"):
            c_type_b = eval_b.get("cleared_type", "청산")
            c_pct_b = eval_b.get("cleared_pnl_pct", 0.0)
            default_title_b = f"[{c_type_b} 청산 완료] ({c_pct_b:+.2f}%)"
        else:
            default_title_b = "더미 진입 완료" if eval_b.get("status") == "DUMMY_CONSUMED" else ("포지션 유지 (매매 없음)" if qty_b > 0 else "관망 (신호 미발생)")
        action_title_b = escape_tg(eval_b.get("action_title") or default_title_b)
        entry_reason_b = escape_tg(eval_b.get("entry_reason") or eval_b.get("reason") or "진입 조건 정보 없음")
        exit_reason_b = escape_tg(eval_b.get("exit_reason") or ("미보유 (해당 없음)" if qty_b <= 0 else "청산 조건 미충족"))
        action_detail_b = escape_tg(eval_b.get("action_detail") or ("포지션 미보유 유지" if qty_b <= 0 else f"기존 포지션({qty_b:,.4f} Qty) 홀딩 유지"))

        # 포지션 동기화 요약
        sync_status = escape_tg(format_reconcile_summary(recon_results))
        qty_a_str = f"{qty_a:,.4f}" if qty_a > 0 else "0"
        qty_b_str = f"{qty_b:,.4f}" if qty_b > 0 else "0"
        all_synced = bool(recon_results) and all(r.status in ("OK", "SYNCED", "SKIPPED_PAPER") for r in recon_results.values())
        sync_line = f"정상 (롱 {qty_a_str} / 숏 {qty_b_str} 일치)" if all_synced else sync_status

        summary_msg = (
            f"<b>📊 [Bitget V3 Switch 일봉 보고]</b>\n\n"
            f"<b>🔥 [핵심 지표]</b>\n"
            f"• <b>일봉 변동률:</b> {change_icon} <b>{candle_change:+.2f}%</b> (종가 ${eval_close:,.2f}){candle_basis}\n"
            f"• <b>실시간 현재가:</b> {price_line}\n"
            f"• <b>이평선:</b> SMA5 ${indicators['sma5']:,.2f} / SMA60 ${indicators['sma60']:,.2f} ({trend_text})\n"
            f"• <b>계좌:</b> ${total_balance:,.2f} (가용: ${free_margin:,.2f})\n"
            f"• <b>평가손익:</b> {pos_summary_a} / {pos_summary_b}\n"
            f"• <b>1.0 Unit:</b> {unit_line}\n\n"
            f"<b>🎯 [금일 집행 및 판단 결과]</b>\n\n"
            f"🔵 <b>전략 A (롱):</b> {action_title_a}\n"
            f"  └ <b>진입:</b> {entry_reason_a}\n"
            f"  └ <b>청산:</b> {exit_reason_a}\n"
            f"  └ <b>조치:</b> {action_detail_a}\n\n"
            f"🔴 <b>전략 B (숏):</b> {action_title_b}\n"
            f"  └ <b>진입:</b> {entry_reason_b}\n"
            f"  └ <b>청산:</b> {exit_reason_b}\n"
            f"  └ <b>조치:</b> {action_detail_b}\n\n"
            f"<b>📦 [전략별 보유 및 진행 현황]</b>\n\n"
            f"🔵 <b>전략 A (Long | Cycle #{updated_state_a.get('cycle_id')}){trailing_info_a}</b>\n"
            f"  • 포지션: {pos_line_a}\n"
        )
        if qty_a > 0:
            summary_msg += f"  • <b>평가손익:</b> {pnl_str_a}\n"
        summary_msg += (
            f"  • <b>누적 실현손익:</b> {cum_pnl_a:+,.2f} USD{last_closed_a_str}\n"
            f"  • <b>차수:</b> [더미 {dummy_a}/2 진입 | 실전 {exec_a}/13]\n"
            f"    {gauge_bar_a}\n\n"
            f"🔴 <b>전략 B (Short | Cycle #{updated_state_b.get('cycle_id')}){trailing_info_b}</b>\n"
            f"  • 포지션: {pos_line_b}\n"
        )
        if qty_b > 0:
            summary_msg += f"  • <b>평가손익:</b> {pnl_str_b}\n"
        summary_msg += (
            f"  • <b>누적 실현손익:</b> {cum_pnl_b:+,.2f} USD{last_closed_b_str}\n"
            f"  • <b>차수:</b> [더미 {dummy_b}/2 진입 | 실전 {exec_b}/13]\n"
            f"    {gauge_bar_b}\n\n"
            f"<b>🛡️ [시스템 상태]</b>\n"
            f"• <b>포지션 동기화:</b> {sync_line}"
        )

        notifier.send_message(summary_msg)

        try:
            excel_logger.update_pnl_chart()
        except Exception as e_chart:
            logger.warning(f"[Pipeline] 엑셀 손익차트 갱신 실패: {e_chart}")

        logger.info(f"[Pipeline] 일봉 마감 실행 파이프라인 정상 종료 (소요시간: {datetime.now() - start_time})")
        return True

    except Exception as e:
        logger.error(f"[Pipeline] 파이프라인 실행 중 심각한 예외 발생: {e}", exc_info=True)
        if isinstance(e, TradingError):
            send_live_halt_alert("일봉 파이프라인", str(e))
        else:
            kst_err = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')
            notifier.send_message(
                f"<b>🚨 [Bitget V3 Switch 에러 발생]</b>\n• <b>발생 일시:</b> {kst_err}\n• <b>내용:</b> {escape_tg(str(e))}"
            )
        return False


def run_stage2_pipeline() -> bool:
    """
    5분 주기 2단계 트레일링 매도 전용 감시 파이프라인
    - A 또는 B 전략이 trailing_mode 활성 중일 때만 4시간봉 5MA 및 최소 이익 보존선 판정
    - 오류 시 텔레그램 경보 (동일 오류 1시간 쿨다운)
    """
    if not cycle_manager.is_any_trailing_active():
        return True

    logger.info("[Pipeline 4H] 2단계 트레일링 매도 감시 파이프라인 시작")
    try:
        if cycle_manager.load_error:
            raise StateCorruptedError(cycle_manager.load_error)

        bitget_client.setup_exchange()
        df_4h = load_ohlcv_or_abort(TIMEFRAME_4H, CANDLE_LIMIT_4H, MIN_4H_CANDLES)

        recon_results = run_reconciliation(stage="2단계 감시", throttle=True)
        blocked = {name for name, r in recon_results.items() if r.blocked}

        logs = trader.process_stage2_monitoring(df_4h, blocked=blocked)
        if logs:
            logger.info(f"[Pipeline 4H] 2단계 트레일링 처리 결과: {len(logs)}건")
        return True
    except Exception as e:
        logger.error(f"[Pipeline 4H] 2단계 감시 파이프라인 오류: {e}", exc_info=True)
        if should_send_alert("stage2_pipeline_error", ALERT_COOLDOWN_SEC):
            send_live_halt_alert("2단계 트레일링 감시", str(e))
        return False


def run_cron_tick() -> bool:
    """
    Crontab 5분 주기 통합 틱 핸들러 (권장: 3-59/5 * * * *)
    - 15:54 ~ 15:59 ET (미국장 마감 직전, 서머타임 자동): 하루 1회 일봉 평가 및 매수 진입 실행
      (DAILY_SIGNAL_SOURCE=bitget 롤백 시 09:10 ~ 09:30 KST)
    - 그 외 시간대:
      - 2단계 트레일링 활성 중일 때: 1시간봉 2단계 매도 감시
      - 2단계 트레일링 비활성 시: 즉시 종료
    """
    kst_now = datetime.now(KST)
    logger.info(f"[CronTick] 5분 주기 감시 틱 실행 (KST {kst_now.strftime('%Y-%m-%d %H:%M:%S')})")

    if is_daily_evaluation_window():
        if USE_LS:
            # 미국 주말은 평가 시간대 판정에서 제외됨, 휴장일은 run_pipeline 0단계에서 판정
            expected_eval_candle = datetime.now(ET).strftime("%Y-%m-%d")
        else:
            if WEEKEND_FREEZE_ENABLED and kst_now.weekday() in WEEKEND_FREEZE_DAYS:
                day_name = "일요일" if kst_now.weekday() == 6 else "월요일"
                logger.info(f"[CronTick] 주말 미국 본장 휴장({day_name} 09:10 KST)으로 일봉 마감 평가를 동결(Freeze)합니다.")
                return True
            expected_eval_candle = (kst_now - timedelta(days=1)).strftime("%Y-%m-%d 09:00:00")
        if not cycle_manager.is_daily_evaluated(expected_eval_candle):
            logger.info(f"[CronTick] 일봉 평가 시간대({EVAL_WINDOW_LABEL}) 충족 -> 일봉 마감 파이프라인 실행")
            return run_pipeline()
        logger.info(f"[CronTick] 금일 일봉({expected_eval_candle}) 평가 완료 상태 -> 중복 파이프라인 진입 생략")

    if cycle_manager.is_any_trailing_active():
        logger.info("[CronTick] 2단계 트레일링 감시 활성 상태 -> 4시간봉 감시 파이프라인 실행")
        return run_stage2_pipeline()

    # 포지션 미보유/1단계 상태에서도 매시 첫 5분 틱(0~7분)마다 4시간봉 추세 상태를 대시보드에 동기화
    if kst_now.minute < 8:
        sync_4h_trend_state(context_label="CronTick")

    logger.info("[CronTick] 2단계 트레일링 미활성 및 일봉 평가 시간대 아님 -> 즉시 종료")
    return True


def run_reconcile_cli(adopt: bool) -> int:
    """--reconcile [--adopt-exchange] 수동 대조/복구 CLI"""
    if PAPER_TRADING:
        print("[Reconcile] 모의 매매(PAPER_TRADING=true) 모드에서는 거래소 대조가 필요 없습니다.")
        return 0

    bitget_client.setup_exchange()
    if cycle_manager.load_error and not adopt:
        print(f"[Reconcile] state.json 손상 상태입니다: {cycle_manager.load_error}")
        print("[Reconcile] 복구하려면 --reconcile --adopt-exchange 를 실행하세요.")
        return 1

    results = reconciler.reconcile(auto_sync=False)
    for r in results.values():
        print(
            f"[{r.strategy}] {r.status} | state {r.state_qty:,.4f} @ ${r.state_avg:,.4f} | "
            f"거래소 {r.exchange_qty:,.4f} @ ${r.exchange_avg:,.4f} | {r.detail}"
        )

    if not adopt:
        return 1 if any(r.blocked for r in results.values()) else 0

    answer = input("거래소 포지션 값을 state.json에 채택합니다. 계속하시겠습니까? (y/N): ").strip().lower()
    if answer != "y":
        print("[Reconcile] 취소되었습니다.")
        return 1

    date_str = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')
    adopted = reconciler.adopt_exchange(date_str)
    for r in adopted.values():
        print(f"[{r.strategy}] 채택 결과: {r.status} - {r.detail}")
    return 0


def run_test_order(side_key: str) -> int:
    """--test-order long|short: 최소 금액 실주문 진입 후 즉시 청산 (state/엑셀 미기록)"""
    if PAPER_TRADING:
        print("[TestOrder] PAPER_TRADING=true 상태입니다. 실주문 테스트는 PAPER_TRADING=false 에서만 실행됩니다.")
        return 1

    name = "strategy_A" if side_key == "long" else "strategy_B"
    cfg = SIDE_CFG[name]
    bitget_client.setup_exchange()

    long_pos, short_pos = bitget_client.fetch_open_positions()
    existing = long_pos if side_key == "long" else short_pos
    if float(existing.get("contracts", 0.0)) > 0:
        print("[TestOrder] 해당 방향에 이미 포지션이 있어 테스트를 중단합니다 (평단 왜곡 방지).")
        return 1

    price = float(bitget_client.exchange.fetch_ticker(SYMBOL)['last'])
    qty = max(bitget_client.get_min_amount(), (MIN_ORDER_USD * 1.4) / price)
    print(f"[TestOrder] {side_key.upper()} 진입 후 즉시 청산: 약 {qty:.4f} Qty (≈ ${qty * price:,.2f}) @ ${price:,.2f}")
    if input("실제 주문을 실행합니다. 계속하시겠습니까? (y/N): ").strip().lower() != "y":
        print("[TestOrder] 취소되었습니다.")
        return 1

    opened = bitget_client.create_hedge_order(
        side=cfg["open_side"], amount=qty, pos_side=cfg["pos_side"], is_close=False, ref_price=price
    )
    print(f"[TestOrder] 진입 체결: {opened.filled} @ ${opened.average:,.4f} (수수료 ${opened.fee:,.4f}, 주문 {opened.order_id})")
    try:
        closed = bitget_client.create_hedge_order(
            side=cfg["close_side"], amount=opened.filled, pos_side=cfg["pos_side"], is_close=True, ref_price=price
        )
    except Exception as e:
        print(f"[TestOrder] ❌ 청산 실패: {e} → Bitget 앱에서 테스트 포지션을 수동으로 청산하세요.")
        return 1
    print(f"[TestOrder] 청산 체결: {closed.filled} @ ${closed.average:,.4f} (수수료 ${closed.fee:,.4f}, 주문 {closed.order_id})")
    print("[TestOrder] state.json/엑셀에는 기록하지 않았습니다. Bitget 앱에서 해당 방향 포지션 0을 확인하세요.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Bitget V3 Switch 2단계 스마트 트레일링 자동매매 파이프라인")
    parser.add_argument("--run-once", action="store_true", help="일봉 마감 파이프라인을 1회 즉시 실행하고 종료합니다.")
    parser.add_argument("--force", action="store_true", help="--run-once와 함께 사용: 이미 평가한 일봉이어도 재평가합니다. (중복 매매 주의)")
    parser.add_argument("--cron", action="store_true", help="크론탭(3-59/5 * * * *) 5분 주기 틱을 1회 실행하고 종료합니다.")
    parser.add_argument("--stage2", action="store_true", help="4시간봉 2단계 트레일링 감시를 1회 즉시 실행하고 종료합니다.")
    parser.add_argument("--loop", action="store_true", help="5분 주기 상시 백그라운드 스케줄러로 구동합니다.")
    parser.add_argument("--update-chart", action="store_true", help="trade_history.xlsx의 손익차트(종합/V3+/V3-) 시트를 즉시 갱신하고 종료합니다.")
    parser.add_argument("--reconcile", action="store_true", help="state.json과 거래소 실제 포지션을 대조하여 출력합니다.")
    parser.add_argument("--adopt-exchange", action="store_true", help="--reconcile과 함께 사용: 거래소 포지션 값을 state.json에 채택합니다.")
    parser.add_argument("--test-order", choices=["long", "short"], help="최소 금액 실주문 진입 후 즉시 청산 테스트 (state 미기록)")
    args = parser.parse_args()

    if args.update_chart:
        logger.info("[Main] --update-chart 모드로 손익차트 시트 갱신을 실행합니다.")
        excel_logger.update_pnl_chart()
        sys.exit(0)

    # 실전 모드 필수 설정 검증 (누락 시 어떤 매매 경로도 실행하지 않음)
    missing = validate_live_config()
    if missing:
        msg = f"실전 모드(PAPER_TRADING=false)인데 필수 API 설정이 비어 있습니다: {', '.join(missing)}"
        logger.critical(f"[Main] {msg}")
        send_live_halt_alert("시작 설정 검증", msg)
        sys.exit(2)

    if args.reconcile:
        sys.exit(run_reconcile_cli(adopt=args.adopt_exchange))
    elif args.test_order:
        sys.exit(run_test_order(args.test_order))
    elif args.cron:
        logger.info("[Main] --cron 모드로 5분 주기 틱을 실행합니다.")
        success = run_cron_tick()
        sys.exit(0 if success else 1)
    elif args.stage2:
        logger.info("[Main] --stage2 모드로 2단계 트레일링 감시를 즉시 실행합니다.")
        success = run_stage2_pipeline()
        sys.exit(0 if success else 1)
    elif args.loop:
        logger.info("[Main] --loop 5분 상시 백그라운드 스케줄러 구동을 시작합니다.")
        run_cron_tick()
        while True:
            time.sleep(300)
            cycle_manager.reload()   # 수동 복구(--adopt-exchange) 등 디스크 변경 반영
            run_cron_tick()
    else:
        logger.info("[Main] --run-once 단일 일봉 실행 모드로 파이프라인을 시작합니다.")
        success = run_pipeline(force=args.force)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
