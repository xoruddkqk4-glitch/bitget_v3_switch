"""
manager/trader.py
Bitget Hedge Mode 주문(Long/Short 분리 진입/청산) 집행 및 매도 우선 원칙 제어 모듈 (PRD 섹션 2, 4, 6)
"""

from typing import Dict, Any, List
from datetime import datetime, timezone, timedelta
import pandas as pd
from config import UNIT_DIVISOR, PRESERVATION_BUFFER_PCT
from api.bitget_client import BitgetClient, bitget_client
from manager.cycle_manager import CycleManager, cycle_manager
from strategy.indicator import IndicatorCalculator
from strategy.strategy_a_long import StrategyALong
from strategy.strategy_b_short import StrategyBShort
from utils.excel_logger import ExcelLogger, excel_logger
from utils.logger import logger
from utils.notifier import TelegramNotifier, notifier

KST = timezone(timedelta(hours=9))


class Trader:
    """
    일봉 마감 시점 A/B 전략 조건 평가 및 주문 집행 통합 총괄 매니저
    """
    def __init__(
        self,
        client: BitgetClient = bitget_client,
        cycles: CycleManager = cycle_manager,
        excel: ExcelLogger = excel_logger,
        telegram: TelegramNotifier = notifier
    ):
        self.client = client
        self.cycles = cycles
        self.excel = excel
        self.notifier = telegram
        self.strategy_a = StrategyALong()
        self.strategy_b = StrategyBShort()
        self.last_total_balance: float = 0.0
        self.last_unit_base_usd: float = 0.0

    def process_daily_cycle(self, indicators: Dict[str, Any]) -> List[str]:
        """
        일봉 마감 시점 파이프라인 전체를 처리합니다.
        1. 계좌 총 평가 잔고 및 실시간 포지션 확인
        2. 전고점/전저점 갱신
        3. [매도 우선] 전략 A 롱 청산 및 전략 B 숏 청산 평가
        4. [매수 진입] 전략 A 롱 진입 및 전략 B 숏 진입 평가 (당일 청산 시 1차 더미로 이관)
        """
        date_str = indicators.get('candle_date', '')
        current_price = indicators['current_price']
        execution_logs: List[str] = []

        # 1. 총 평가 잔고 및 1 Unit 기준금액 산출
        total_balance = self.client.fetch_total_balance()
        unit_base_usd = total_balance / UNIT_DIVISOR
        self.last_total_balance = total_balance
        self.last_unit_base_usd = unit_base_usd
        logger.info(f"[Trader] 계좌 총 평가 잔고: ${total_balance:,.2f} | 1.0 Unit 기본금액: ${unit_base_usd:,.2f}")

        # 2. 전고점/전저점 갱신
        self.cycles.update_price_extremes("strategy_A", current_price)
        self.cycles.update_price_extremes("strategy_B", current_price)

        state_a = self.cycles.get_strategy_state("strategy_A")
        state_b = self.cycles.get_strategy_state("strategy_B")

        cleared_today_a = False
        cleared_today_b = False

        # ======================================================================
        # 3. [매도 우선 원칙] 1차 청산(Exit) 평가 및 집행 / 2단계 트레일링 돌입
        # ======================================================================

        # 3-1. A 전략 (Long) 1차 청산 평가
        exit_a_met, action_a, exit_a_reason, target_a_price = self.strategy_a.check_stage1_exit_signal(
            current_price=current_price,
            indicators=indicators,
            cycle_info=state_a
        )
        if exit_a_met:
            if action_a == "ENTER_STAGE2":
                # 상승장/과낙폭 익절: 즉시 청산하지 않고 2단계 4H 5MA 감시 모드 활성화
                self.cycles.set_trailing_mode(
                    strategy_name="strategy_A",
                    target_price=target_a_price,
                    base_price=target_a_price,
                    reason=exit_a_reason,
                    date_str=date_str
                )
                execution_logs.append(f"🟡 [롱 2단계 트레일링 돌입] {exit_a_reason} (최소보존선: ${target_a_price:,.2f})")
            elif action_a == "IMMEDIATE_EXIT":
                # 완만한 하락장 본전 탈출: 지체 없이 즉시 시장가 전량 청산
                logger.info(f"[Trader] [Strategy A] 롱 즉시 전량 청산 충족! 사유: {exit_a_reason}")
                qty_to_close = state_a.get('total_qty', 0.0)
                avg_p = state_a.get('avg_price', 0.0)
                pnl_usd = (current_price - avg_p) * qty_to_close if avg_p > 0 else 0.0
                pnl_pct = ((current_price / avg_p) - 1.0) * 100.0 if avg_p > 0 else 0.0

                if qty_to_close > 0:
                    self.cycles.record_cycle_realized_pnl("strategy_A", pnl_usd, pnl_pct)
                    self.client.create_hedge_order(
                        side='sell',
                        amount=qty_to_close,
                        pos_side='long',
                        is_close=True
                    )
                self.excel.append_record(
                    date_str=date_str,
                    strategy_name="strategy_A",
                    event_type="SELL_CLEAR",
                    price=current_price,
                    qty=qty_to_close,
                    unit_label="ALL_CLEAR",
                    dummy_status="0 / 2",
                    total_balance=total_balance,
                    note=f"{exit_a_reason} (목표: ${target_a_price:,.2f}, 손익: {pnl_usd:+,.2f} USD, {pnl_pct:+.2f}%)"
                )
                self.cycles.reset_cycle("strategy_A", date_str=date_str, transfer_to_dummy1=False)
                cleared_today_a = True
                execution_logs.append(
                    f"🟢 [롱 즉시 전량 청산] {exit_a_reason} (체결가: ${current_price:,.2f}, 수량: {qty_to_close:,.4f}, 확정손익: {pnl_usd:+,.2f} USD, {pnl_pct:+.2f}%)"
                )
        else:
            logger.info(f"[Trader] [Strategy A] 롱 1차 청산 조건 미충족 ({exit_a_reason})")

        # 3-2. B 전략 (Short) 1차 청산 평가
        exit_b_met, action_b, exit_b_reason, target_b_price = self.strategy_b.check_stage1_exit_signal(
            current_price=current_price,
            indicators=indicators,
            cycle_info=state_b
        )
        if exit_b_met:
            if action_b == "ENTER_STAGE2":
                # 상승눌림/과반등 익절: 2단계 4H 5MA 감시 모드 활성화
                self.cycles.set_trailing_mode(
                    strategy_name="strategy_B",
                    target_price=target_b_price,
                    base_price=target_b_price,
                    reason=exit_b_reason,
                    date_str=date_str
                )
                execution_logs.append(f"🟡 [숏 2단계 트레일링 돌입] {exit_b_reason} (최소보존선: ${target_b_price:,.2f})")
            elif action_b == "IMMEDIATE_EXIT":
                # 완만한 반등장 본전 탈출: 즉시 시장가 전량 청산
                logger.info(f"[Trader] [Strategy B] 숏 즉시 전량 청산 충족! 사유: {exit_b_reason}")
                qty_to_close = state_b.get('total_qty', 0.0)
                avg_p = state_b.get('avg_price', 0.0)
                pnl_usd = (avg_p - current_price) * qty_to_close if avg_p > 0 else 0.0
                pnl_pct = ((avg_p - current_price) / avg_p) * 100.0 if avg_p > 0 else 0.0

                if qty_to_close > 0:
                    self.cycles.record_cycle_realized_pnl("strategy_B", pnl_usd, pnl_pct)
                    self.client.create_hedge_order(
                        side='buy',
                        amount=qty_to_close,
                        pos_side='short',
                        is_close=True
                    )
                self.excel.append_record(
                    date_str=date_str,
                    strategy_name="strategy_B",
                    event_type="SELL_CLEAR",
                    price=current_price,
                    qty=qty_to_close,
                    unit_label="ALL_CLEAR",
                    dummy_status="0 / 2",
                    total_balance=total_balance,
                    note=f"{exit_b_reason} (목표: ${target_b_price:,.2f}, 손익: {pnl_usd:+,.2f} USD, {pnl_pct:+.2f}%)"
                )
                self.cycles.reset_cycle("strategy_B", date_str=date_str, transfer_to_dummy1=False)
                cleared_today_b = True
                execution_logs.append(
                    f"🔴 [숏 즉시 전량 청산] {exit_b_reason} (체결가: ${current_price:,.2f}, 수량: {qty_to_close:,.4f}, 확정손익: {pnl_usd:+,.2f} USD, {pnl_pct:+.2f}%)"
                )
        else:
            logger.info(f"[Trader] [Strategy B] 숏 1차 청산 조건 미충족 ({exit_b_reason})")


        # ======================================================================
        # 4. [매수 진입 단계] 진입(Entry) 신호 평가 및 집행
        # ======================================================================

        # 4-1. A 전략 (Long) 진입 평가
        if state_a.get("trailing_mode", False):
            logger.info("[Trader] [Strategy A] 2단계 트레일링 감시 진행 중이므로 신규 롱 진입 스킵")
        else:
            entry_a_met, entry_a_reason = self.strategy_a.check_entry_signal(
                current_candle=indicators,
                prev_candle={'close': indicators['prev_close']}
            )
            if entry_a_met:
                logger.info(f"[Trader] [Strategy A] 롱 진입 신호 발생: {entry_a_reason}")
                if cleared_today_a:
                    # PRD 명세: 청산 완료 당일 발생한 매수 신호는 신규 싸이클의 1차 더미로 이관 처리
                    self.cycles.reset_cycle("strategy_A", date_str=date_str, transfer_to_dummy1=True)
                    self.excel.append_record(
                        date_str=date_str,
                        strategy_name="strategy_A",
                        event_type="BUY_DUMMY",
                        price=None,
                        qty=None,
                        unit_label="더미 1회차 (이관)",
                        dummy_status="1 / 2",
                        total_balance=total_balance,
                        note=f"{entry_a_reason} (당일 청산 후 신규 싸이클 1차 더미로 이관)"
                    )
                    execution_logs.append(f"🟡 [롱 1차 더미 이관] 당일 청산 발생으로 신규 싸이클 1차 더미 소진 처리")
                else:
                    action_type, weight, unit_label = self.cycles.determine_buy_action("strategy_A")
                    if action_type == "DUMMY":
                        dummy_count = self.cycles.consume_dummy("strategy_A", date_str=date_str)
                        self.excel.append_record(
                            date_str=date_str,
                            strategy_name="strategy_A",
                            event_type="BUY_DUMMY",
                            price=None,
                            qty=None,
                            unit_label=unit_label,
                            dummy_status=f"{dummy_count} / 2",
                            total_balance=total_balance,
                            note=f"{entry_a_reason} (더미 방어 소진)"
                        )
                        execution_logs.append(f"⚪ [롱 더미 소진] {unit_label} 처리 완료 (현재 더미 {dummy_count}/2)")
                    else:
                        # 실전 매수 집행
                        target_usd = unit_base_usd * weight
                        target_qty = target_usd / current_price if current_price > 0 else 0.0
                        order = self.client.create_hedge_order(
                            side='buy',
                            amount=target_qty,
                            pos_side='long',
                            is_close=False
                        )
                        self.cycles.record_executed_unit(
                            strategy_name="strategy_A",
                            price=current_price,
                            qty=target_qty,
                            date_str=date_str
                        )
                        self.excel.append_record(
                            date_str=date_str,
                            strategy_name="strategy_A",
                            event_type="BUY_UNIT",
                            price=current_price,
                            qty=target_qty,
                            unit_label=unit_label,
                            dummy_status="2 / 2",
                            total_balance=total_balance,
                            note=f"{entry_a_reason} (투입금액: ${target_usd:,.2f})"
                        )
                        execution_logs.append(f"🟢 [롱 실전 매수] {unit_label}: ${current_price:,.2f}에 {target_qty:,.4f} Qty (${target_usd:,.1f})")
            else:
                logger.info("[Trader] [Strategy A] 롱 진입 신호 없음")

        # 4-2. B 전략 (Short) 진입 평가
        if state_b.get("trailing_mode", False):
            logger.info("[Trader] [Strategy B] 2단계 트레일링 감시 진행 중이므로 신규 숏 진입 스킵")
        else:
            entry_b_met, entry_b_reason = self.strategy_b.check_entry_signal(
                current_candle=indicators,
                prev_candle={'close': indicators['prev_close']}
            )
            if entry_b_met:
                logger.info(f"[Trader] [Strategy B] 숏 진입 신호 발생: {entry_b_reason}")
                if cleared_today_b:
                    # PRD 명세: 청산 완료 당일 발생한 매도(숏) 신호는 신규 싸이클의 1차 더미로 이관 처리
                    self.cycles.reset_cycle("strategy_B", date_str=date_str, transfer_to_dummy1=True)
                    self.excel.append_record(
                        date_str=date_str,
                        strategy_name="strategy_B",
                        event_type="BUY_DUMMY",
                        price=None,
                        qty=None,
                        unit_label="더미 1회차 (이관)",
                        dummy_status="1 / 2",
                        total_balance=total_balance,
                        note=f"{entry_b_reason} (당일 청산 후 신규 싸이클 1차 더미로 이관)"
                    )
                    execution_logs.append(f"🟡 [숏 1차 더미 이관] 당일 청산 발생으로 신규 싸이클 1차 더미 소진 처리")
                else:
                    action_type, weight, unit_label = self.cycles.determine_buy_action("strategy_B")
                    if action_type == "DUMMY":
                        dummy_count = self.cycles.consume_dummy("strategy_B", date_str=date_str)
                        self.excel.append_record(
                            date_str=date_str,
                            strategy_name="strategy_B",
                            event_type="BUY_DUMMY",
                            price=None,
                            qty=None,
                            unit_label=unit_label,
                            dummy_status=f"{dummy_count} / 2",
                            total_balance=total_balance,
                            note=f"{entry_b_reason} (더미 방어 소진)"
                        )
                        execution_logs.append(f"⚪ [숏 더미 소진] {unit_label} 처리 완료 (현재 더미 {dummy_count}/2)")
                    else:
                        # 실전 숏 매도 집행
                        target_usd = unit_base_usd * weight
                        target_qty = target_usd / current_price if current_price > 0 else 0.0
                        order = self.client.create_hedge_order(
                            side='sell',
                            amount=target_qty,
                            pos_side='short',
                            is_close=False
                        )
                        self.cycles.record_executed_unit(
                            strategy_name="strategy_B",
                            price=current_price,
                            qty=target_qty,
                            date_str=date_str
                        )
                        self.excel.append_record(
                            date_str=date_str,
                            strategy_name="strategy_B",
                            event_type="BUY_UNIT",
                            price=current_price,
                            qty=target_qty,
                            unit_label=unit_label,
                            dummy_status="2 / 2",
                            total_balance=total_balance,
                            note=f"{entry_b_reason} (투입금액: ${target_usd:,.2f})"
                        )
                        execution_logs.append(f"🔴 [숏 실전 진입] {unit_label}: ${current_price:,.2f}에 {target_qty:,.4f} Qty (${target_usd:,.1f})")
            else:
                logger.info("[Trader] [Strategy B] 숏 진입 신호 없음")

        return execution_logs

    def process_stage2_monitoring(self, df_4h: pd.DataFrame) -> List[str]:
        """
        5분 주기 2단계 트레일링 매도 전용 감시 파이프라인
        A 또는 B 전략이 trailing_mode 활성 상태일 때 4시간봉 5MA 및 최소 이익 보존선을 검사하여 청산 집행
        """
        execution_logs: List[str] = []
        if df_4h.empty or len(df_4h) < 5:
            logger.warning("[Trader 4H] 4시간봉 캔들이 부족하여 2단계 감시를 건너뜁니다.")
            return execution_logs

        indicators_4h = IndicatorCalculator.calculate_4h(df_4h)
        current_price = indicators_4h['current_price']
        date_str = indicators_4h.get('last_candle_date', '')
        total_balance = self.client.fetch_total_balance()

        state_a = self.cycles.get_strategy_state("strategy_A")
        state_b = self.cycles.get_strategy_state("strategy_B")

        # 1. Strategy A (Long) 2단계 감시
        if state_a.get("trailing_mode", False):
            trailing_base = state_a.get("trailing_base_price", 0.0)
            exit_2_met, reason_2 = self.strategy_a.check_stage2_exit_signal(
                current_price=current_price,
                indicators_4h=indicators_4h,
                trailing_base_price=trailing_base,
                buffer_pct=PRESERVATION_BUFFER_PCT
            )
            if exit_2_met:
                logger.info(f"[Trader 4H] [Strategy A] 롱 2단계 트레일링 청산 조건 충족! {reason_2}")
                qty_to_close = state_a.get('total_qty', 0.0)
                avg_p = state_a.get('avg_price', 0.0)
                realized_pnl = (current_price - avg_p) * qty_to_close if avg_p > 0 else 0.0
                realized_pct = ((current_price / avg_p) - 1.0) * 100.0 if avg_p > 0 else 0.0

                if qty_to_close > 0:
                    self.cycles.record_cycle_realized_pnl("strategy_A", realized_pnl, realized_pct)
                    self.client.create_hedge_order(
                        side='sell',
                        amount=qty_to_close,
                        pos_side='long',
                        is_close=True
                    )
                self.excel.append_record(
                    date_str=date_str,
                    strategy_name="strategy_A",
                    event_type="SELL_CLEAR",
                    price=current_price,
                    qty=qty_to_close,
                    unit_label="ALL_CLEAR_TRAILING",
                    dummy_status="0 / 2",
                    total_balance=total_balance,
                    note=f"2단계 트레일링 청산: {reason_2} (손익: {realized_pnl:+,.2f} USD, {realized_pct:+.2f}%)"
                )
                self.cycles.reset_cycle("strategy_A", date_str=date_str, transfer_to_dummy1=False)

                kst_str = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')
                log_msg = f"🟢 [롱 2단계 트레일링 익절 완료] {reason_2} (체결가: ${current_price:,.2f}, 수량: {qty_to_close:,.4f}, 확정손익: {realized_pnl:+,.2f} USD, {realized_pct:+.2f}%)"
                execution_logs.append(log_msg)

                unit_base = total_balance / UNIT_DIVISOR
                msg = (
                    f"<b>📊 [Bitget V3 Switch 2단계 롱 청산 보고]</b>\n\n"
                    f"• <b>기준 일시:</b> {kst_str}\n"
                    f"• <b>종목:</b> {self.client.symbol}\n"
                    f"• <b>계좌 총 평가 잔고:</b> ${total_balance:,.2f}\n"
                    f"• <b>1.0 Unit 기본 금액:</b> ${unit_base:,.2f}\n"
                    f"• <b>청산 내용:</b> {log_msg}\n"
                    f"• <b>확정 수익금:</b> <b>{realized_pnl:+,.2f} USD ({realized_pct:+.2f}%)</b>"
                )
                self.notifier.send_message(msg)

        # 2. Strategy B (Short) 2단계 감시
        if state_b.get("trailing_mode", False):
            trailing_base = state_b.get("trailing_base_price", 0.0)
            exit_2_met, reason_2 = self.strategy_b.check_stage2_exit_signal(
                current_price=current_price,
                indicators_4h=indicators_4h,
                trailing_base_price=trailing_base,
                buffer_pct=PRESERVATION_BUFFER_PCT
            )
            if exit_2_met:
                logger.info(f"[Trader 4H] [Strategy B] 숏 2단계 트레일링 청산 조건 충족! {reason_2}")
                qty_to_close = state_b.get('total_qty', 0.0)
                avg_p = state_b.get('avg_price', 0.0)
                realized_pnl = (avg_p - current_price) * qty_to_close if avg_p > 0 else 0.0
                realized_pct = ((avg_p - current_price) / avg_p) * 100.0 if avg_p > 0 else 0.0

                if qty_to_close > 0:
                    self.cycles.record_cycle_realized_pnl("strategy_B", realized_pnl, realized_pct)
                    self.client.create_hedge_order(
                        side='buy',
                        amount=qty_to_close,
                        pos_side='short',
                        is_close=True
                    )
                self.excel.append_record(
                    date_str=date_str,
                    strategy_name="strategy_B",
                    event_type="SELL_CLEAR",
                    price=current_price,
                    qty=qty_to_close,
                    unit_label="ALL_CLEAR_TRAILING",
                    dummy_status="0 / 2",
                    total_balance=total_balance,
                    note=f"2단계 트레일링 청산: {reason_2} (손익: {realized_pnl:+,.2f} USD, {realized_pct:+.2f}%)"
                )
                self.cycles.reset_cycle("strategy_B", date_str=date_str, transfer_to_dummy1=False)

                kst_str = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')
                log_msg = f"🔴 [숏 2단계 트레일링 익절 완료] {reason_2} (체결가: ${current_price:,.2f}, 수량: {qty_to_close:,.4f}, 확정손익: {realized_pnl:+,.2f} USD, {realized_pct:+.2f}%)"
                execution_logs.append(log_msg)

                unit_base = total_balance / UNIT_DIVISOR
                msg = (
                    f"<b>📊 [Bitget V3 Switch 2단계 숏 청산 보고]</b>\n\n"
                    f"• <b>기준 일시:</b> {kst_str}\n"
                    f"• <b>종목:</b> {self.client.symbol}\n"
                    f"• <b>계좌 총 평가 잔고:</b> ${total_balance:,.2f}\n"
                    f"• <b>1.0 Unit 기본 금액:</b> ${unit_base:,.2f}\n"
                    f"• <b>청산 내용:</b> {log_msg}\n"
                    f"• <b>확정 수익금:</b> <b>{realized_pnl:+,.2f} USD ({realized_pct:+.2f}%)</b>"
                )
                self.notifier.send_message(msg)

        return execution_logs


trader = Trader()

