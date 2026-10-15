"""
manager/trader.py
Bitget Hedge Mode 주문(Long/Short 분리 진입/청산) 집행 및 매도 우선 원칙 제어 모듈 (PRD 섹션 2, 4, 6)
- 모든 상태/손익/엑셀 기록은 체결 확인된 OrderResult(실체결가·수량·수수료) 기준으로 수행합니다.
- 전략(A/B)별 처리는 서로 격리되어 한 전략의 오류가 다른 전략 평가를 중단시키지 않습니다.
"""

from typing import Dict, Any, List, Optional, Set, Tuple
from datetime import datetime, timezone, timedelta
import pandas as pd
from config import (
    UNIT_DIVISOR, MAX_EXECUTED_UNITS, MARGIN_SAFETY_RATIO, LEVERAGE, STAGE2_SIGNAL_MODE,
    PRESERVATION_BUFFER_PCT, MIN_ORDER_USD, PARTIAL_FILL_RATIO, ALERT_COOLDOWN_SEC
)
from api.bitget_client import BitgetClient, bitget_client
from api.exceptions import OrderSkipError
from manager.cycle_manager import CycleManager, cycle_manager
from strategy.indicator import IndicatorCalculator
from strategy.strategy_a_long import StrategyALong
from strategy.strategy_b_short import StrategyBShort
from utils.alert_throttle import should_send_alert
from utils.excel_logger import ExcelLogger, excel_logger
from utils.logger import logger
from utils.notifier import TelegramNotifier, notifier
from utils.formatters import format_progress_single_track

KST = timezone(timedelta(hours=9))

STRATEGY_NAMES = ("strategy_A", "strategy_B")

# 전략별 방향/주문/표시 설정 (A=롱, B=숏)
SIDE_CFG: Dict[str, Dict[str, Any]] = {
    "strategy_A": {
        "label": "롱", "opp_label": "숏", "opposite": "strategy_B",
        "pos_side": "long", "open_side": "buy", "close_side": "sell", "direction": 1,
        "emoji": "🟢", "entry_title": "롱 실전 매수", "entry_summary": "매수 체결",
    },
    "strategy_B": {
        "label": "숏", "opp_label": "롱", "opposite": "strategy_A",
        "pos_side": "short", "open_side": "sell", "close_side": "buy", "direction": -1,
        "emoji": "🔴", "entry_title": "숏 실전 진입", "entry_summary": "숏 진입 체결",
    },
}


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
        self.strategies = {"strategy_A": self.strategy_a, "strategy_B": self.strategy_b}
        self.last_total_balance: float = 0.0
        self.last_free_margin: float = 0.0
        self.last_unit_base_usd: float = 0.0
        self.last_eval: Dict[str, Dict[str, Any]] = {name: {} for name in STRATEGY_NAMES}

    # ------------------------------------------------------------------
    # 하위 호환 속성 (main.py 일일 보고서용)
    # ------------------------------------------------------------------
    @property
    def last_eval_a(self) -> Dict[str, Any]:
        return self.last_eval.get("strategy_A", {})

    @property
    def last_eval_b(self) -> Dict[str, Any]:
        return self.last_eval.get("strategy_B", {})

    # ------------------------------------------------------------------
    # 공통 유틸
    # ------------------------------------------------------------------
    @staticmethod
    def _escape(text: str) -> str:
        """텔레그램 HTML 파싱 오류 방지를 위한 특수문자 이스케이프"""
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _refresh_balance(self) -> Tuple[float, float]:
        """총 평가 잔고/가용 잔고를 조회해 캐시합니다. (실전 실패 시 예외 전파)"""
        total_balance, free_margin = self.client.fetch_account_balance()
        self.last_total_balance = total_balance
        self.last_free_margin = free_margin
        self.last_unit_base_usd = total_balance / UNIT_DIVISOR
        return total_balance, free_margin

    def _set_eval(self, strategy_name: str, signal_met: bool, status: str, reason: str, summary: str):
        self.last_eval[strategy_name] = {
            "signal_met": signal_met,
            "status": status,
            "reason": reason,
            "summary": summary,
        }

    def _resolve_close_qty(self, strategy_name: str, state_qty: float) -> float:
        """청산 수량: 실전은 거래소 실제 포지션 수량, 모의는 state 수량"""
        if self.client.paper_trading:
            return state_qty
        long_pos, short_pos = self.client.fetch_open_positions()
        pos = long_pos if SIDE_CFG[strategy_name]["pos_side"] == "long" else short_pos
        exchange_qty = float(pos.get("contracts", 0.0))
        if abs(exchange_qty - state_qty) > 1e-9:
            logger.info(
                f"[Trader] [{strategy_name}] 청산 수량은 거래소 실제 포지션 기준 사용: "
                f"state {state_qty:,.4f} → 거래소 {exchange_qty:,.4f}"
            )
        return exchange_qty

    # ------------------------------------------------------------------
    # 일봉 마감 파이프라인
    # ------------------------------------------------------------------
    def process_daily_cycle(self, indicators: Dict[str, Any], blocked: Optional[Set[str]] = None) -> List[str]:
        """
        일봉 마감 시점 파이프라인 전체를 처리합니다.
        1. 계좌 총 평가 잔고 및 가용 잔고 확인
        2. 전고점/전저점 갱신
        3. [매도 우선] 전략 A 롱 / 전략 B 숏 1차 청산 평가 (전략별 오류 격리)
        4. [매수 진입] 전략 A 롱 / 전략 B 숏 진입 평가 (당일 청산 시 1차 더미로 이관)
        blocked: 포지션 대조 불일치(MISMATCH)로 매매를 차단할 전략명 집합
        """
        blocked = set(blocked or set())
        date_str = indicators.get('candle_date', '')
        current_price = indicators['current_price']
        execution_logs: List[str] = []
        self.last_eval = {name: {} for name in STRATEGY_NAMES}

        # 1. 총 평가 잔고, 실시간 가용 잔고 및 1 Unit 기준금액 산출
        total_balance, free_margin = self._refresh_balance()
        logger.info(
            f"[Trader] 계좌 총 평가 잔고: ${total_balance:,.2f} | 가용 잔고: ${free_margin:,.2f} | "
            f"1.0 Unit 기준금액: 단일 ${self.last_unit_base_usd:,.2f} / 헤지 ${free_margin / UNIT_DIVISOR:,.2f}"
        )

        # 2. 전고점/전저점 갱신 (차단 전략 제외)
        for name in STRATEGY_NAMES:
            if name not in blocked:
                self.cycles.update_price_extremes(name, current_price)

        cleared_today = {name: False for name in STRATEGY_NAMES}
        failed: Set[str] = set()

        # 3. [매도 우선 원칙] 1차 청산 평가
        for name in STRATEGY_NAMES:
            label = SIDE_CFG[name]["label"]
            if name in blocked:
                execution_logs.append(f"⛔ [{label} 매매 차단] 거래소 포지션 불일치로 청산/진입 평가를 생략했습니다.")
                self._set_eval(
                    name, False, "BLOCKED_MISMATCH", "거래소 포지션 불일치",
                    "⛔ 포지션 불일치로 매매 차단 (python main.py --reconcile 확인 필요)"
                )
                continue
            try:
                cleared_today[name] = self._evaluate_stage1_exit(name, indicators, date_str, execution_logs)
            except OrderSkipError as e:
                logger.warning(f"[Trader] [{name}] {label} 청산 주문 스킵: {e}")
                execution_logs.append(f"⚠️ [{label} 청산 주문 스킵] {e}")
            except Exception as e:
                logger.error(f"[Trader] [{name}] {label} 청산 처리 실패: {e}", exc_info=True)
                execution_logs.append(f"❌ [{label} 청산 처리 실패] {e}")
                failed.add(name)
                self._set_eval(name, False, "EXIT_FAILED", str(e), f"❌ 청산 처리 실패로 진입 평가 생략 ({e})")

        # 4. [매수 진입 단계] 진입 평가
        for name in STRATEGY_NAMES:
            if name in blocked or name in failed:
                continue
            label = SIDE_CFG[name]["label"]
            try:
                self._evaluate_entry(name, indicators, date_str, cleared_today[name], execution_logs)
            except OrderSkipError as e:
                logger.warning(f"[Trader] [{name}] {label} 진입 주문 스킵: {e}")
                execution_logs.append(f"⚠️ [{label} 진입 주문 스킵] {e}")
                self._set_eval(name, True, "SKIPPED_ORDER", str(e), f"⚠️ 주문 스킵 ({e})")
            except Exception as e:
                logger.error(f"[Trader] [{name}] {label} 진입 처리 실패: {e}", exc_info=True)
                execution_logs.append(f"❌ [{label} 진입 처리 실패] {e}")
                self._set_eval(name, True, "ENTRY_FAILED", str(e), f"❌ 진입 처리 실패 ({e})")

        return execution_logs

    def _evaluate_stage1_exit(
        self,
        strategy_name: str,
        indicators: Dict[str, Any],
        date_str: str,
        execution_logs: List[str]
    ) -> bool:
        """1차 청산 평가. 즉시 전량 청산이 완료되면 True(당일 청산)를 반환합니다."""
        cfg = SIDE_CFG[strategy_name]
        label = cfg["label"]
        strategy = self.strategies[strategy_name]
        current_price = indicators['current_price']
        state = self.cycles.get_strategy_state(strategy_name)

        exit_met, action, exit_reason, target_price = strategy.check_stage1_exit_signal(
            current_price=current_price,
            indicators=indicators,
            cycle_info=state
        )

        # 2단계 트레일링 진행 중: 재돌입/즉시청산 없이 보존선을 유리한 방향으로만 갱신 (래칫)
        if state.get("trailing_mode", False):
            self._handle_trailing_ratchet(
                strategy_name, exit_met, action, exit_reason, target_price, date_str, execution_logs
            )
            return False

        if not exit_met:
            logger.info(f"[Trader] [{strategy_name}] {label} 1차 청산 조건 미충족 ({exit_reason})")
            return False

        if action == "ENTER_STAGE2":
            # 상승장/과낙폭(과반등) 익절: 즉시 청산하지 않고 2단계 4H 5MA 감시 모드 활성화
            self.cycles.set_trailing_mode(
                strategy_name=strategy_name,
                target_price=target_price,
                base_price=target_price,
                reason=exit_reason,
                date_str=date_str
            )
            execution_logs.append(f"🟡 [{label} 2단계 트레일링 돌입] {exit_reason} (최소보존선: ${target_price:,.2f})")
            return False

        if action == "IMMEDIATE_EXIT":
            logger.info(f"[Trader] [{strategy_name}] {label} 즉시 전량 청산 충족! 사유: {exit_reason}")
            closed, log_msg = self._close_position(
                strategy_name=strategy_name,
                ref_price=current_price,
                date_str=date_str,
                reason=exit_reason,
                unit_label="ALL_CLEAR",
                log_title=f"{label} 즉시 전량 청산",
                target_price=target_price
            )
            execution_logs.append(log_msg)
            return closed

        return False

    def _handle_trailing_ratchet(
        self,
        strategy_name: str,
        exit_met: bool,
        action: str,
        exit_reason: str,
        target_price: float,
        date_str: str,
        execution_logs: List[str]
    ):
        """
        트레일링 감시 중 일봉 재평가 처리:
        - ENTER_STAGE2 신규 목표가가 기존 보존선보다 유리할 때만 보존선 갱신 (롱 상향 / 숏 하향)
        - IMMEDIATE_EXIT / NONE / 불리한 목표가는 무시 (보존선 유지, 청산은 4H 감시가 담당)
        """
        cfg = SIDE_CFG[strategy_name]
        label = cfg["label"]
        if not (exit_met and action == "ENTER_STAGE2" and target_price > 0):
            logger.info(f"[Trader] [{strategy_name}] {label} 트레일링 감시 중 - 일봉 재평가 결과 무시 ({action}: {exit_reason})")
            return

        improved, old_base, new_base = self.cycles.ratchet_trailing_base(
            strategy_name, target_price, exit_reason, date_str
        )
        if not improved:
            logger.info(
                f"[Trader] [{strategy_name}] {label} 보존선 유지 (기존 ${old_base:,.2f}, 신규 후보 ${target_price:,.2f} - 불리한 방향)"
            )
            return

        direction_txt = "상향" if cfg["direction"] > 0 else "하향"
        execution_logs.append(f"🔒 [{label} 보존선 {direction_txt}] ${old_base:,.2f} → ${new_base:,.2f} ({exit_reason})")
        self.excel.append_record(
            date_str=date_str,
            strategy_name=strategy_name,
            event_type="TRAIL_RATCHET",
            price=None,
            qty=None,
            unit_label="보존선 갱신",
            dummy_status="-",
            total_balance=self.last_total_balance,
            note=f"최소 이익 보존선 {direction_txt}: ${old_base:,.2f} → ${new_base:,.2f} ({exit_reason})",
            auto_update_chart=False
        )

    def _close_position(
        self,
        strategy_name: str,
        ref_price: float,
        date_str: str,
        reason: str,
        unit_label: str,
        log_title: str,
        note_prefix: str = "",
        target_price: Optional[float] = None
    ) -> Tuple[bool, str]:
        """
        보유 포지션 전량 청산: 주문 → 체결 확인 → 실현손익 기록 → 엑셀 기록 → 싸이클 초기화 순서로 처리합니다.
        Returns: (전량 청산 완료 여부, 실행 로그 메시지)
        """
        cfg = SIDE_CFG[strategy_name]
        label = cfg["label"]
        state = self.cycles.get_strategy_state(strategy_name)
        avg_price = float(state.get('avg_price', 0.0))
        state_qty = float(state.get('total_qty', 0.0))
        close_qty = self._resolve_close_qty(strategy_name, state_qty)
        target_txt = f"목표: ${target_price:,.2f}, " if target_price else ""

        if close_qty <= 0:
            self.excel.append_record(
                date_str=date_str,
                strategy_name=strategy_name,
                event_type="SELL_CLEAR",
                price=None,
                qty=None,
                unit_label=unit_label,
                dummy_status="0 / 2",
                total_balance=self.last_total_balance,
                note=f"{note_prefix}{reason} (보유 수량 0 - 주문 없이 싸이클 초기화)",
                realized_pnl=0.0,
                pnl_pct=0.0
            )
            self.cycles.reset_cycle(strategy_name, date_str=date_str, transfer_to_dummy1=False)
            return True, f"⚪ [{log_title}] 보유 수량 0 → 주문 없이 싸이클 초기화 ({reason})"

        # 1) 주문 → 체결 확인 (실패 시 예외 전파: 상태/손익 기록 없음)
        result = self.client.create_hedge_order(
            side=cfg["close_side"],
            amount=close_qty,
            pos_side=cfg["pos_side"],
            is_close=True,
            ref_price=ref_price
        )
        fill_price = result.average if result.average > 0 else ref_price
        filled = result.filled
        direction = cfg["direction"]

        # 2) 실현손익 (체결가 기준, 청산 수수료 차감)
        if avg_price > 0:
            pnl_usd = (fill_price - avg_price) * filled * direction - result.fee
            pnl_pct = ((fill_price / avg_price) - 1.0) * 100.0 * direction
        else:
            pnl_usd = -result.fee
            pnl_pct = 0.0
        self.cycles.record_cycle_realized_pnl(strategy_name, pnl_usd, pnl_pct)
        note = (
            f"{note_prefix}{reason} ({target_txt}손익: {pnl_usd:+,.2f} USD, {pnl_pct:+.2f}%, "
            f"수수료: ${result.fee:,.4f})"
        )

        # 3) 부분 체결: 잔량 유지 (싸이클 초기화 보류)
        if filled < close_qty * PARTIAL_FILL_RATIO:
            self.cycles.reduce_position(strategy_name, filled, date_str)
            self.excel.append_record(
                date_str=date_str,
                strategy_name=strategy_name,
                event_type="SELL_CLEAR_PARTIAL",
                price=fill_price,
                qty=filled,
                unit_label=f"{unit_label}_PARTIAL",
                dummy_status="-",
                total_balance=self.last_total_balance,
                note=f"[부분 체결 {filled:,.4f}/{close_qty:,.4f}] {note}",
                realized_pnl=pnl_usd,
                pnl_pct=pnl_pct
            )
            msg = (
                f"⚠️ [{label} 부분 청산] 요청 {close_qty:,.4f} / 체결 {filled:,.4f} Qty "
                f"(체결가 ${fill_price:,.2f}, 손익 {pnl_usd:+,.2f} USD) - 잔량 유지, 다음 실행에서 재평가"
            )
            self.notifier.send_message(f"<b>⚠️ [Bitget V3 Switch 부분 청산 경보]</b>\n• {self._escape(msg)}")
            return False, msg

        # 4) 전량 청산: 엑셀 기록 → 싸이클 초기화
        self.excel.append_record(
            date_str=date_str,
            strategy_name=strategy_name,
            event_type="SELL_CLEAR",
            price=fill_price,
            qty=filled,
            unit_label=unit_label,
            dummy_status="0 / 2",
            total_balance=self.last_total_balance,
            note=note,
            realized_pnl=pnl_usd,
            pnl_pct=pnl_pct
        )
        self.cycles.reset_cycle(strategy_name, date_str=date_str, transfer_to_dummy1=False)
        msg = (
            f"{cfg['emoji']} [{log_title}] {reason} (체결가: ${fill_price:,.2f}, 수량: {filled:,.4f}, "
            f"확정손익: {pnl_usd:+,.2f} USD, {pnl_pct:+.2f}%)"
        )
        return True, msg

    def _evaluate_entry(
        self,
        strategy_name: str,
        indicators: Dict[str, Any],
        date_str: str,
        cleared_today: bool,
        execution_logs: List[str]
    ):
        """진입 신호 평가 및 더미 소진/실전 매수 집행"""
        cfg = SIDE_CFG[strategy_name]
        label = cfg["label"]
        strategy = self.strategies[strategy_name]

        # 최신 state 재조회 (당일 트레일링 돌입 여부 반영)
        state = self.cycles.get_strategy_state(strategy_name)
        if state.get("trailing_mode", False):
            reason = "2단계 트레일링 감시 진행 중 (신규 진입 스킵)"
            logger.info(f"[Trader] [{strategy_name}] {reason}")
            self._set_eval(strategy_name, False, "TRAILING_ACTIVE", reason, "🟡 2단계 트레일링 감시 진행 중 (신규 진입 스킵)")
            return

        entry_met, entry_reason = strategy.check_entry_signal(
            current_candle=indicators,
            prev_candle={'close': indicators['prev_close']}
        )
        if not entry_met:
            logger.info(f"[Trader] [{strategy_name}] {label} 진입 신호 없음: {entry_reason}")
            self._set_eval(strategy_name, False, "NO_SIGNAL", entry_reason, f"⚪ 조건 미충족 ({entry_reason})")
            return

        logger.info(f"[Trader] [{strategy_name}] {label} 진입 신호 발생: {entry_reason}")

        if cleared_today:
            # PRD 명세: 청산 완료 당일 발생한 신호는 신규 싸이클의 1차 더미로 이관 (cycle_id 추가 증가 없음)
            self.cycles.consume_dummy(strategy_name, date_str=date_str)
            self.excel.append_record(
                date_str=date_str,
                strategy_name=strategy_name,
                event_type="BUY_DUMMY",
                price=None,
                qty=None,
                unit_label="더미 1회차 (이관)",
                dummy_status="1 / 2",
                total_balance=self.last_total_balance,
                note=f"{entry_reason} (당일 청산 후 신규 싸이클 1차 더미로 이관)"
            )
            bar = format_progress_single_track(1, 0, include_counts=False)
            execution_logs.append(f"🟡 [{label} 1차 더미 이관] 당일 청산으로 신규 싸이클 1차 더미 소진: {bar}")
            self._set_eval(strategy_name, True, "DUMMY_TRANSFERRED", entry_reason, f"🟡 1차 더미 이관 ({entry_reason})")
            return

        action_type, weight, unit_label = self.cycles.determine_buy_action(strategy_name)
        if action_type == "MAX_REACHED":
            reason = f"{entry_reason} (최대 {MAX_EXECUTED_UNITS}회차 도달 - 추가 매수 중단)"
            logger.info(f"[Trader] [{strategy_name}] {label} {reason}")
            execution_logs.append(f"⏸️ [{label} 추가 매수 중단] 최대 {MAX_EXECUTED_UNITS}회차 도달 ({entry_reason})")
            self._set_eval(
                strategy_name, True, "SKIPPED_MAX_UNITS", reason,
                f"⏸️ 최대 {MAX_EXECUTED_UNITS}회차 도달로 추가 매수 중단"
            )
            return

        if action_type == "DUMMY":
            dummy_count = self.cycles.consume_dummy(strategy_name, date_str=date_str)
            self.excel.append_record(
                date_str=date_str,
                strategy_name=strategy_name,
                event_type="BUY_DUMMY",
                price=None,
                qty=None,
                unit_label=unit_label,
                dummy_status=f"{dummy_count} / 2",
                total_balance=self.last_total_balance,
                note=f"{entry_reason} (더미 방어 소진)"
            )
            bar = format_progress_single_track(dummy_count, 0, include_counts=False)
            execution_logs.append(f"⚪ [{label} 더미 소진] {unit_label} 완료: {bar} (더미 {dummy_count}/2)")
            self._set_eval(strategy_name, True, "DUMMY_CONSUMED", entry_reason, f"⚪ {unit_label} 소진 ({entry_reason})")
            return

        self._execute_real_entry(
            strategy_name=strategy_name,
            current_price=indicators['current_price'],
            date_str=date_str,
            weight=weight,
            unit_label=unit_label,
            entry_reason=entry_reason,
            execution_logs=execution_logs
        )

    def _execute_real_entry(
        self,
        strategy_name: str,
        current_price: float,
        date_str: str,
        weight: float,
        unit_label: str,
        entry_reason: str,
        execution_logs: List[str]
    ):
        """실전 진입: 유닛 산정 → 주문 → 체결 확인 → 상태/엑셀 기록 → 상대 더미 리셋 → 잔고 재조회"""
        cfg = SIDE_CFG[strategy_name]
        label = cfg["label"]
        opposite = cfg["opposite"]

        # 상대편 포지션 보유 여부에 따른 동적 유닛 기준 (Hedge 모드 양방향 상태)
        opposite_qty = float(self.cycles.get_strategy_state(opposite).get("total_qty", 0.0))
        if opposite_qty > 0:
            unit_base_usd = self.last_free_margin / UNIT_DIVISOR
            basis_label = "가용 잔고(헤지)"
        else:
            unit_base_usd = self.last_total_balance / UNIT_DIVISOR
            basis_label = "총 평가 잔고(단일)"

        target_usd = unit_base_usd * weight

        # 가용 잔고 상한: 증거금 부족으로 인한 주문 거절 방지 (가용 잔고 × 레버리지 × 안전비율)
        margin_cap_usd = self.last_free_margin * LEVERAGE * MARGIN_SAFETY_RATIO
        if target_usd > margin_cap_usd:
            logger.warning(
                f"[Trader] [{strategy_name}] 주문금액 ${target_usd:,.2f} → 가용 잔고 상한 ${margin_cap_usd:,.2f}로 축소 "
                f"(가용 ${self.last_free_margin:,.2f} × {LEVERAGE}x × {MARGIN_SAFETY_RATIO})"
            )
            target_usd = margin_cap_usd
            basis_label = f"{basis_label}, 가용상한 적용"

        # 최소 주문금액 가드
        if target_usd < MIN_ORDER_USD:
            msg = (
                f"⚠️ [{label} 주문 스킵] {unit_label}: 주문금액(${target_usd:,.2f})이 "
                f"최소 한도(${MIN_ORDER_USD:,.2f}) 미만입니다. ({basis_label} 기준)"
            )
            logger.warning(f"[Trader] {msg}")
            execution_logs.append(msg)
            self._set_eval(
                strategy_name, True, "SKIPPED_MIN_ORDER",
                f"{entry_reason} (주문금액 ${target_usd:,.2f} < 최소한도 ${MIN_ORDER_USD:,.2f})",
                f"⚠️ 주문 스킵 (주문금액 ${target_usd:,.1f} < ${MIN_ORDER_USD:,.1f})"
            )
            return

        # 1) 주문 → 체결 확인
        target_qty = target_usd / current_price if current_price > 0 else 0.0
        result = self.client.create_hedge_order(
            side=cfg["open_side"],
            amount=target_qty,
            pos_side=cfg["pos_side"],
            is_close=False,
            ref_price=current_price
        )
        fill_price = result.average if result.average > 0 else current_price
        invested_usd = fill_price * result.filled

        # 2) 체결 기준 상태 기록
        self.cycles.record_executed_unit(
            strategy_name=strategy_name,
            price=fill_price,
            qty=result.filled,
            date_str=date_str
        )

        # 3) 엑셀 기록
        self.excel.append_record(
            date_str=date_str,
            strategy_name=strategy_name,
            event_type="BUY_UNIT",
            price=fill_price,
            qty=result.filled,
            unit_label=unit_label,
            dummy_status="2 / 2",
            total_balance=self.last_total_balance,
            note=f"{entry_reason} (투입: ${invested_usd:,.2f}, {basis_label}, 수수료: ${result.fee:,.4f})"
        )

        executed = self.cycles.get_strategy_state(strategy_name).get("executed_units", 0)
        bar = format_progress_single_track(2, executed, include_counts=False)
        execution_logs.append(
            f"{cfg['emoji']} [{cfg['entry_title']}] {unit_label}: ${fill_price:,.2f}에 {result.filled:,.4f} Qty "
            f"(${invested_usd:,.1f}) [{basis_label}] | {bar} ({executed}/{MAX_EXECUTED_UNITS})"
        )
        self._set_eval(
            strategy_name, True, "BUY_EXECUTED", entry_reason,
            f"{cfg['emoji']} {unit_label} {cfg['entry_summary']} ({entry_reason})"
        )

        # 4) 체결 직후: 최초 1회차 실전 매수 진입 시에만 상대 전략의 과거 유령 더미 초기화
        if executed == 1 and self.cycles.reset_incomplete_dummy(opposite, trigger_source=strategy_name, date_str=date_str):
            bar_reset = format_progress_single_track(0, 0, include_counts=False)
            opp_label = cfg["opp_label"]
            execution_logs.append(
                f"🔄 [{opp_label} 더미 초기화] {label} 최초 실전 매수 체결로 {opp_label} 과거 유령 더미 리셋: {bar_reset} (더미 0/2)"
            )
            self.excel.append_record(
                date_str=date_str,
                strategy_name=opposite,
                event_type="RESET_DUMMY",
                price=None,
                qty=None,
                unit_label="더미 초기화 (0/2)",
                dummy_status="0 / 2",
                total_balance=self.last_total_balance,
                note=f"{label} 최초 실전 매수 체결에 따른 {opp_label} 과거 유령 더미 0/2 초기화"
            )

        # 5) 다음 전략 유닛 산정을 위한 잔고 재조회 (실패해도 이미 체결된 주문 기록은 유지)
        try:
            self._refresh_balance()
        except Exception as e:
            logger.warning(f"[Trader] 체결 후 잔고 재조회 실패 (이전 잔고 값 유지): {e}")

    # ------------------------------------------------------------------
    # 5분 주기 2단계 트레일링 감시
    # ------------------------------------------------------------------
    def process_stage2_monitoring(self, df_4h: pd.DataFrame, blocked: Optional[Set[str]] = None) -> List[str]:
        """
        5분 주기 2단계 트레일링 매도 전용 감시 파이프라인
        A 또는 B 전략이 trailing_mode 활성 상태일 때 4시간봉 5MA 및 최소 이익 보존선을 검사하여 청산 집행
        """
        blocked = set(blocked or set())
        execution_logs: List[str] = []
        if df_4h.empty or len(df_4h) < 6:
            logger.warning("[Trader 4H] 4시간봉 캔들이 부족하여 2단계 감시를 건너뜁니다.")
            return execution_logs

        indicators_4h = IndicatorCalculator.calculate_4h(df_4h)
        current_price = indicators_4h['current_price']
        date_str = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')   # 실제 청산 시각(KST)
        self._refresh_balance()

        for name in STRATEGY_NAMES:
            state = self.cycles.get_strategy_state(name)
            if not state.get("trailing_mode", False):
                continue
            cfg = SIDE_CFG[name]
            label = cfg["label"]
            if name in blocked:
                logger.warning(f"[Trader 4H] [{name}] 포지션 불일치로 2단계 감시 청산을 생략합니다.")
                continue

            try:
                exit_met, reason_2 = self.strategies[name].check_stage2_exit_signal(
                    current_price=current_price,
                    indicators_4h=indicators_4h,
                    trailing_base_price=state.get("trailing_base_price", 0.0),
                    buffer_pct=PRESERVATION_BUFFER_PCT,
                    trailing_activated_ts=int(state.get("trailing_activated_ts", 0) or 0),
                    signal_mode=STAGE2_SIGNAL_MODE
                )
                if not exit_met:
                    logger.info(f"[Trader 4H] [{name}] {label} 2단계 유지: {reason_2}")
                    continue

                logger.info(f"[Trader 4H] [{name}] {label} 2단계 트레일링 청산 조건 충족! {reason_2}")
                closed, log_msg = self._close_position(
                    strategy_name=name,
                    ref_price=current_price,
                    date_str=date_str,
                    reason=reason_2,
                    unit_label="ALL_CLEAR_TRAILING",
                    log_title=f"{label} 2단계 트레일링 익절 완료",
                    note_prefix="2단계 트레일링 청산: "
                )
                execution_logs.append(log_msg)

                if closed:
                    last_state = self.cycles.get_strategy_state(name)
                    realized_pnl = last_state.get('last_realized_pnl', 0.0)
                    realized_pct = last_state.get('last_realized_pct', 0.0)
                    kst_str = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')
                    unit_base = self.last_total_balance / UNIT_DIVISOR
                    msg = (
                        f"<b>📊 [Bitget V3 Switch 2단계 {label} 청산 보고]</b>\n\n"
                        f"• <b>기준 일시:</b> {kst_str}\n"
                        f"• <b>종목:</b> {self.client.symbol}\n"
                        f"• <b>계좌 총 평가 잔고:</b> ${self.last_total_balance:,.2f}\n"
                        f"• <b>1.0 Unit 기본 금액:</b> ${unit_base:,.2f}\n"
                        f"• <b>청산 내용:</b> {self._escape(log_msg)}\n"
                        f"• <b>확정 수익금:</b> <b>{realized_pnl:+,.2f} USD ({realized_pct:+.2f}%)</b>"
                    )
                    self.notifier.send_message(msg)
            except OrderSkipError as e:
                logger.warning(f"[Trader 4H] [{name}] {label} 2단계 청산 주문 스킵: {e}")
                execution_logs.append(f"⚠️ [{label} 2단계 청산 주문 스킵] {e}")
            except Exception as e:
                logger.error(f"[Trader 4H] [{name}] {label} 2단계 청산 실패: {e}", exc_info=True)
                execution_logs.append(f"❌ [{label} 2단계 청산 실패] {e}")
                if should_send_alert(f"stage2_close_fail_{name}", ALERT_COOLDOWN_SEC):
                    self.notifier.send_message(
                        f"<b>🚨 [Bitget V3 Switch 2단계 {label} 청산 실패]</b>\n"
                        f"• <b>사유:</b> {self._escape(str(e))}\n"
                        f"• 다음 5분 틱에서 자동 재시도합니다."
                    )

        return execution_logs


trader = Trader()
