"""
manager/trader.py
Bitget Hedge Mode 주문(Long/Short 분리 진입/청산) 집행 및 매도 우선 원칙 제어 모듈 (PRD 섹션 2, 4, 6)
"""

from typing import Dict, Any, List
from config import UNIT_DIVISOR
from api.bitget_client import BitgetClient, bitget_client
from manager.cycle_manager import CycleManager, cycle_manager
from strategy.strategy_a_long import StrategyALong
from strategy.strategy_b_short import StrategyBShort
from utils.excel_logger import ExcelLogger, excel_logger
from utils.logger import logger
from utils.notifier import TelegramNotifier, notifier


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
        logger.info(f"[Trader] 계좌 총 평가 잔고: ${total_balance:,.2f} | 1.0 Unit 기본금액: ${unit_base_usd:,.2f}")

        # 2. 전고점/전저점 갱신
        self.cycles.update_price_extremes("strategy_A", current_price)
        self.cycles.update_price_extremes("strategy_B", current_price)

        state_a = self.cycles.get_strategy_state("strategy_A")
        state_b = self.cycles.get_strategy_state("strategy_B")

        cleared_today_a = False
        cleared_today_b = False

        # ======================================================================
        # 3. [매도 우선 원칙] 청산(Exit) 평가 및 집행
        # ======================================================================

        # 3-1. A 전략 (Long) 청산 평가
        exit_a_met, exit_a_reason, target_a_price = self.strategy_a.check_exit_signal(
            current_price=current_price,
            indicators=indicators,
            cycle_info=state_a
        )
        if exit_a_met:
            logger.info(f"[Trader] [Strategy A] 롱 전량 청산 조건 충족! 사유: {exit_a_reason}")
            qty_to_close = state_a.get('total_qty', 0.0)
            if qty_to_close > 0:
                self.client.create_hedge_order(
                    side='sell',
                    amount=qty_to_close,
                    pos_side='long',
                    is_close=True
                )
            # 엑셀 기록 및 사이클 리셋
            self.excel.append_record(
                date_str=date_str,
                strategy_name="strategy_A",
                event_type="SELL_CLEAR",
                price=current_price,
                qty=qty_to_close,
                unit_label="ALL_CLEAR",
                dummy_status="0 / 2",
                total_balance=total_balance,
                note=f"{exit_a_reason} (목표: ${target_a_price:,.2f})"
            )
            self.cycles.reset_cycle("strategy_A", date_str=date_str, transfer_to_dummy1=False)
            cleared_today_a = True
            execution_logs.append(f"🟢 [롱 전량 청산] {exit_a_reason} (체결가: ${current_price:,.2f}, 수량: {qty_to_close})")
        else:
            logger.info(f"[Trader] [Strategy A] 롱 청산 조건 미충족 ({exit_a_reason})")

        # 3-2. B 전략 (Short) 청산 평가
        exit_b_met, exit_b_reason, target_b_price = self.strategy_b.check_exit_signal(
            current_price=current_price,
            indicators=indicators,
            cycle_info=state_b
        )
        if exit_b_met:
            logger.info(f"[Trader] [Strategy B] 숏 전량 청산 조건 충족! 사유: {exit_b_reason}")
            qty_to_close = state_b.get('total_qty', 0.0)
            if qty_to_close > 0:
                self.client.create_hedge_order(
                    side='buy',
                    amount=qty_to_close,
                    pos_side='short',
                    is_close=True
                )
            # 엑셀 기록 및 사이클 리셋
            self.excel.append_record(
                date_str=date_str,
                strategy_name="strategy_B",
                event_type="SELL_CLEAR",
                price=current_price,
                qty=qty_to_close,
                unit_label="ALL_CLEAR",
                dummy_status="0 / 2",
                total_balance=total_balance,
                note=f"{exit_b_reason} (목표: ${target_b_price:,.2f})"
            )
            self.cycles.reset_cycle("strategy_B", date_str=date_str, transfer_to_dummy1=False)
            cleared_today_b = True
            execution_logs.append(f"🔴 [숏 전량 청산] {exit_b_reason} (체결가: ${current_price:,.2f}, 수량: {qty_to_close})")
        else:
            logger.info(f"[Trader] [Strategy B] 숏 청산 조건 미충족 ({exit_b_reason})")

        # ======================================================================
        # 4. [매수 진입 단계] 진입(Entry) 신호 평가 및 집행
        # ======================================================================

        # 4-1. A 전략 (Long) 진입 평가
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


trader = Trader()
