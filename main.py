"""
main.py
bitget_v3_switch 자동매매 시스템 메인 엔트리 포인트 (PRD 섹션 6)
일봉 마감 시점(09:00 KST / 00:00 UTC) 1회 실행 파이프라인
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
    PAPER_TRADING,
    BITGET_API_KEY
)
from utils.logger import logger
from utils.notifier import notifier
from api.bitget_client import bitget_client
from strategy.indicator import IndicatorCalculator
from manager.cycle_manager import cycle_manager
from manager.trader import trader


def generate_mock_ohlcv(limit: int = 100) -> pd.DataFrame:
    """네트워크 연결 불가 시 정적 검증 및 시뮬레이션을 위한 샘플 일봉 데이터를 생성합니다."""
    logger.info("[Mock] 시뮬레이션용 가상 100일 일봉 데이터를 생성합니다.")
    dates = [datetime.now(timezone.utc) - timedelta(days=i) for i in reversed(range(limit))]
    base_price = 30.0
    records = []
    for i, dt in enumerate(dates):
        # 변동성 부여
        import math
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


def run_pipeline() -> bool:
    """
    일봉 마감 시점 통합 실행 파이프라인
    1. 거래소 환경 설정 및 검증
    2. 일봉 OHLCV 수집
    3. 기술적 지표 산출
    4. 매도 우선 청산 및 매수 진입 처리
    5. 텔레그램 결과 보고
    """
    start_time = datetime.now()
    logger.info("=" * 70)
    logger.info(f"[Pipeline] 일봉 마감 실행 파이프라인 시작 ({start_time.strftime('%Y-%m-%d %H:%M:%S')})")
    logger.info("=" * 70)

    try:
        # 1. 거래소 환경 점검
        bitget_client.setup_exchange()

        # 2. OHLCV 수집 (실패 시 모의 데이터 폴백)
        try:
            df = bitget_client.fetch_ohlcv(symbol=SYMBOL, timeframe=TIMEFRAME, limit=CANDLE_LIMIT)
            if df.empty or len(df) < 61:
                logger.warning("[Pipeline] 수집된 캔들 수가 부족하여 모의 데이터로 대체합니다.")
                df = generate_mock_ohlcv(CANDLE_LIMIT)
        except Exception as e:
            logger.warning(f"[Pipeline] 네트워크/API 수집 실패 ({e}), 시뮬레이션 데이터로 진행합니다.")
            df = generate_mock_ohlcv(CANDLE_LIMIT)

        # 3. 싸이클 최고/최저가 로드 및 지표 산출
        state_a = cycle_manager.get_strategy_state("strategy_A")
        state_b = cycle_manager.get_strategy_state("strategy_B")

        indicators = IndicatorCalculator.calculate(
            df=df,
            cycle_peak=state_a.get("cycle_peak", 0.0),
            cycle_trough=state_b.get("cycle_trough", 0.0)
        )

        # 4. Trader 주문 집행 및 매도 우선 평가
        logs = trader.process_daily_cycle(indicators)

        # 5. 상태 요약 및 텔레그램 발송
        updated_state_a = cycle_manager.get_strategy_state("strategy_A")
        updated_state_b = cycle_manager.get_strategy_state("strategy_B")

        summary_msg = (
            f"<b>📊 [Bitget V3 Switch 일봉 마감 보고]</b>\n\n"
            f"• <b>종목:</b> {SYMBOL}\n"
            f"• <b>일봉 기준일:</b> {indicators.get('candle_date')}\n"
            f"• <b>현재가:</b> ${indicators['current_price']:,.2f} "
            f"(캔들: {indicators['candle_change']*100:+.2f}%, 전일비: {indicators['daily_change']*100:+.2f}%)\n"
            f"• <b>SMA5:</b> ${indicators['sma5']:,.2f} | <b>SMA60:</b> ${indicators['sma60']:,.2f} "
            f"({'상승' if indicators['is_sma60_rising'] else '하락'})\n\n"
            f"<b>[전략 A - Long DCA]</b>\n"
            f"• 싸이클 #{updated_state_a.get('cycle_id')}: 더미 {updated_state_a.get('dummy_count')}/2, "
            f"실행 {updated_state_a.get('executed_units')}유닛, 평단 ${updated_state_a.get('avg_price', 0):,.2f}\n\n"
            f"<b>[전략 B - Short DCA]</b>\n"
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


def wait_until_next_daily_close():
    """매일 UTC 00:00:15 (KST 09:00:15) 정시까지 대기합니다."""
    now = datetime.now(timezone.utc)
    target = now.replace(hour=0, minute=0, second=15, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    wait_seconds = (target - now).total_seconds()
    logger.info(f"[Scheduler] 다음 일봉 마감 시점({target.strftime('%Y-%m-%d %H:%M:%S UTC')})까지 {wait_seconds/3600:.2f}시간 대기합니다.")
    time.sleep(wait_seconds)


def main():
    parser = argparse.ArgumentParser(description="Bitget V3 Switch 자동매매 일봉 파이프라인")
    parser.add_argument("--run-once", action="store_true", help="파이프라인을 1회 즉시 실행하고 종료합니다.")
    parser.add_argument("--loop", action="store_true", help="일봉 마감 시간에 맞추어 상시 반복 실행합니다.")
    args = parser.parse_args()

    if args.run_once or not args.loop:
        logger.info("[Main] --run-once 단일 실행 모드로 파이프라인을 시작합니다.")
        success = run_pipeline()
        sys.exit(0 if success else 1)
    else:
        logger.info("[Main] --loop 상시 스케줄링 모드로 백그라운드 구동을 시작합니다.")
        # 첫 구동 시 1회 즉시 실행 후 대기
        run_pipeline()
        while True:
            wait_until_next_daily_close()
            run_pipeline()


if __name__ == "__main__":
    main()
