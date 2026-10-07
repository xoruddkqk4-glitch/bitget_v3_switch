"""
main.py
bitget_v3_switch 2단계 스마트 트레일링 자동매매 시스템 메인 엔트리 포인트
- 일봉 1차 평가: 매일 09:10 ~ 09:15 KST 사이 1회 실행
- 4시간봉 2단계 트레일링 감시: 5분 주기(Crontab 또는 백그라운드 루프) 실행
"""

import argparse
import sys
import time
from datetime import datetime, timezone, timedelta
import pandas as pd

from config import (
    SYMBOL,
    TIMEFRAME,
    CANDLE_LIMIT,
    TIMEFRAME_4H,
    CANDLE_LIMIT_4H,
    DAILY_EVAL_HOUR_KST,
    DAILY_EVAL_START_MIN,
    DAILY_EVAL_END_MIN,
    PAPER_TRADING,
    BITGET_API_KEY
)
from utils.logger import logger
from utils.notifier import notifier
from api.bitget_client import bitget_client
from strategy.indicator import IndicatorCalculator
from manager.cycle_manager import cycle_manager
from manager.trader import trader


def generate_mock_ohlcv(limit: int = 100, timeframe: str = '1d') -> pd.DataFrame:
    """네트워크 연결 불가 시 정적 검증 및 시뮬레이션을 위한 샘플 OHLCV 데이터를 생성합니다."""
    delta = timedelta(hours=4) if timeframe == '4h' else timedelta(days=1)
    dates = [datetime.now(timezone.utc) - (delta * i) for i in reversed(range(limit))]
    base_price = 30.0
    records = []
    import math
    for i, dt in enumerate(dates):
        price = base_price + math.sin(i / 5.0) * 8.0 + (i * 0.1)
        open_p = price - 0.2
        high_p = price + 1.2
        low_p = price - 1.2
        close_p = price
        records.append([
            int(dt.timestamp() * 1000),
            open_p, high_p, low_p, close_p, 50000.0, dt
        ])
    df = pd.DataFrame(records, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'datetime'])
    return df


def is_daily_evaluation_window() -> bool:
    """현재 한국 시각(KST)이 일봉 1차 평가 시간대(09:10 ~ 09:15)인지 검사합니다."""
    kst_now = datetime.now(timezone(timedelta(hours=9)))
    return (
        kst_now.hour == DAILY_EVAL_HOUR_KST and
        DAILY_EVAL_START_MIN <= kst_now.minute <= DAILY_EVAL_END_MIN
    )


def run_pipeline() -> bool:
    """
    일봉 마감 시점 통합 실행 파이프라인 (09:10~09:15 KST)
    1. 거래소 환경 점검
    2. 일봉 OHLCV 수집 및 지표 산출
    3. [매도 우선] 1차 청산 평가 (상황 1/3은 2단계 트레일링 활성화, 상황 2는 즉시 전량 본탈)
    4. [매수 진입] 롱/숏 신규 진입 평가 (트레일링 중인 전략은 진입 스킵)
    5. 텔레그램 일일 보고서 발송
    """
    start_time = datetime.now()
    logger.info("=" * 70)
    logger.info(f"[Pipeline] 일봉 마감 1차 실행 파이프라인 시작 ({start_time.strftime('%Y-%m-%d %H:%M:%S')})")
    logger.info("=" * 70)

    try:
        # 1. 거래소 환경 점검
        bitget_client.setup_exchange()

        # 2. 일봉 OHLCV 수집 (실패 시 모의 데이터 폴백)
        try:
            df = bitget_client.fetch_ohlcv(symbol=SYMBOL, timeframe=TIMEFRAME, limit=CANDLE_LIMIT)
            if df.empty or len(df) < 61:
                logger.warning("[Pipeline] 수집된 일봉 캔들 수가 부족하여 모의 데이터로 대체합니다.")
                df = generate_mock_ohlcv(CANDLE_LIMIT, timeframe=TIMEFRAME)
        except Exception as e:
            logger.warning(f"[Pipeline] 일봉 수집 실패 ({e}), 시뮬레이션 데이터로 진행합니다.")
            df = generate_mock_ohlcv(CANDLE_LIMIT, timeframe=TIMEFRAME)

        # 3. 싸이클 최고/최저가 로드 및 일봉 지표 산출
        state_a = cycle_manager.get_strategy_state("strategy_A")
        state_b = cycle_manager.get_strategy_state("strategy_B")

        indicators = IndicatorCalculator.calculate(
            df=df,
            cycle_peak=state_a.get("cycle_peak", 0.0),
            cycle_trough=state_b.get("cycle_trough", 0.0)
        )

        # 4. Trader 주문 집행 및 1차 청산 / 매수 평가
        logs = trader.process_daily_cycle(indicators)

        # 5. 상태 요약 및 텔레그램 발송
        updated_state_a = cycle_manager.get_strategy_state("strategy_A")
        updated_state_b = cycle_manager.get_strategy_state("strategy_B")

        trailing_info_a = " [2단계 트레일링 감시 중]" if updated_state_a.get("trailing_mode") else ""
        trailing_info_b = " [2단계 트레일링 감시 중]" if updated_state_b.get("trailing_mode") else ""

        summary_msg = (
            f"<b>📊 [Bitget V3 Switch 일봉 1차 마감 보고]</b>\n\n"
            f"• <b>종목:</b> {SYMBOL}\n"
            f"• <b>일봉 기준일:</b> {indicators.get('candle_date')}\n"
            f"• <b>현재가:</b> ${indicators['current_price']:,.2f} "
            f"(캔들: {indicators['candle_change']*100:+.2f}%, 전일비: {indicators['daily_change']*100:+.2f}%)\n"
            f"• <b>SMA5:</b> ${indicators['sma5']:,.2f} | <b>SMA60:</b> ${indicators['sma60']:,.2f} "
            f"({'상승' if indicators['is_sma60_rising'] else '하락'})\n\n"
            f"<b>[전략 A - Long DCA]{trailing_info_a}</b>\n"
            f"• 싸이클 #{updated_state_a.get('cycle_id')}: 더미 {updated_state_a.get('dummy_count')}/2, "
            f"실행 {updated_state_a.get('executed_units')}유닛, 평단 ${updated_state_a.get('avg_price', 0):,.2f}\n\n"
            f"<b>[전략 B - Short DCA]{trailing_info_b}</b>\n"
            f"• 싸이클 #{updated_state_b.get('cycle_id')}: 더미 {updated_state_b.get('dummy_count')}/2, "
            f"실행 {updated_state_b.get('executed_units')}유닛, 평단 ${updated_state_b.get('avg_price', 0):,.2f}\n\n"
            f"<b>[금일 집행 내역]</b>\n"
        )
        if logs:
            summary_msg += "\n".join(f"• {log}" for log in logs)
        else:
            summary_msg += "• 금일 진입/청산 조건 없음 (포지션 유지 또는 관망)"

        notifier.send_message(summary_msg)
        logger.info(f"[Pipeline] 일봉 마감 실행 파이프라인 정상 종료 (소요시간: {datetime.now() - start_time})")
        return True

    except Exception as e:
        logger.error(f"[Pipeline] 파이프라인 실행 중 심각한 예외 발생: {e}", exc_info=True)
        notifier.send_message(f"<b>🚨 [Bitget V3 Switch 에러 발생]</b>\n{str(e)}")
        return False


def run_stage2_pipeline() -> bool:
    """
    5분 주기 2단계 트레일링 매도 전용 감시 파이프라인
    - A 또는 B 전략이 trailing_mode 활성 중일 때만 4시간봉 5MA 및 최소 이익 보존선 판정
    """
    if not cycle_manager.is_any_trailing_active():
        return True

    logger.info("[Pipeline 4H] 2단계 트레일링 매도 감시 파이프라인 시작")
    try:
        bitget_client.setup_exchange()
        try:
            df_4h = bitget_client.fetch_ohlcv(symbol=SYMBOL, timeframe=TIMEFRAME_4H, limit=CANDLE_LIMIT_4H)
            if df_4h.empty or len(df_4h) < 5:
                logger.warning("[Pipeline 4H] 4시간봉 캔들이 부족하여 모의 데이터로 대체합니다.")
                df_4h = generate_mock_ohlcv(CANDLE_LIMIT_4H, timeframe=TIMEFRAME_4H)
        except Exception as e:
            logger.warning(f"[Pipeline 4H] 4시간봉 수집 실패 ({e}), 시뮬레이션 데이터로 진행합니다.")
            df_4h = generate_mock_ohlcv(CANDLE_LIMIT_4H, timeframe=TIMEFRAME_4H)

        logs = trader.process_stage2_monitoring(df_4h)
        if logs:
            logger.info(f"[Pipeline 4H] 2단계 트레일링 청산 집행 완료: {len(logs)}건")
        return True
    except Exception as e:
        logger.error(f"[Pipeline 4H] 2단계 감시 파이프라인 오류: {e}", exc_info=True)
        return False


def run_cron_tick() -> bool:
    """
    Crontab 5분 주기 통합 틱 핸들러 (*/5 * * * *)
    - 09:10 ~ 09:15 KST: 하루 1회 일봉 1차 마감 평가 및 매수 진입 실행
    - 그 외 시간대:
      - 2단계 트레일링 활성 중일 때: 4시간봉 2단계 매도 감시
      - 2단계 트레일링 비활성 시: 0.1초 즉시 종료 (부하 0%)
    """
    kst_now = datetime.now(timezone(timedelta(hours=9)))
    logger.info(f"[CronTick] 5분 주기 감시 틱 실행 (KST {kst_now.strftime('%Y-%m-%d %H:%M:%S')})")

    if is_daily_evaluation_window():
        logger.info("[CronTick] 일봉 1차 평가 시간대(09:10~09:15 KST) 충족 -> 일봉 마감 파이프라인 실행")
        return run_pipeline()

    if cycle_manager.is_any_trailing_active():
        logger.info("[CronTick] 2단계 트레일링 감시 활성 상태 -> 4시간봉 감시 파이프라인 실행")
        return run_stage2_pipeline()

    logger.info("[CronTick] 2단계 트레일링 미활성 및 일봉 평가 시간대 아님 -> 0.1초 즉시 종료")
    return True


def main():
    parser = argparse.ArgumentParser(description="Bitget V3 Switch 2단계 스마트 트레일링 자동매매 파이프라인")
    parser.add_argument("--run-once", action="store_true", help="일봉 마감 파이프라인을 1회 즉시 실행하고 종료합니다.")
    parser.add_argument("--cron", action="store_true", help="크론탭(*/5 * * * *) 5분 주기 틱을 1회 실행하고 종료합니다.")
    parser.add_argument("--stage2", action="store_true", help="4시간봉 2단계 트레일링 감시를 1회 즉시 실행하고 종료합니다.")
    parser.add_argument("--loop", action="store_true", help="5분 주기 상시 백그라운드 스케줄러로 구동합니다.")
    args = parser.parse_args()

    if args.cron:
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
            run_cron_tick()
    else:
        logger.info("[Main] --run-once 단일 일봉 실행 모드로 파이프라인을 시작합니다.")
        success = run_pipeline()
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
