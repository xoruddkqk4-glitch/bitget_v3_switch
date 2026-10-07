"""
manager/cycle_manager.py
A/B 전략별 더미 카운트, 실제 매수 회차(executed_units) 및 평단가/수량 영속성 관리 모듈 (PRD 섹션 2 & 5)
"""

import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Tuple
from config import STATE_FILE, DUMMY_TARGET, WEIGHTED_UNIT_THRESHOLD, WEIGHT_NORMAL, WEIGHT_BOOST
from utils.logger import logger

KST = timezone(timedelta(hours=9))


class CycleManager:
    """
    state.json 파일을 원자적으로 읽고 쓰며 사이클 및 더미 진행 상황을 제어하는 매니저
    """
    def __init__(self, state_file: Path = STATE_FILE):
        self.state_file = Path(state_file)
        self.state: Dict[str, Any] = self._load_state()

    def _get_kst_time_str(self) -> str:
        """현재 한국 시간(KST) 문자열을 반환합니다."""
        return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

    def _default_strategy_state(self, is_long: bool = True) -> Dict[str, Any]:
        state = {
            "cycle_id": 1,
            "dummy_count": 0,
            "executed_units": 0,
            "avg_price": 0.0,
            "total_qty": 0.0,
            "last_action": None,
            "last_action_date": None,
            "trailing_mode": False,
            "trailing_base_price": 0.0,
            "trailing_target_price": 0.0,
            "trailing_reason": "",
            "trailing_triggered_at": None,
            "last_realized_pnl": 0.0,
            "last_realized_pct": 0.0,
            "cumulative_realized_pnl": 0.0
        }
        if is_long:
            state["cycle_peak"] = 0.0
        else:
            state["cycle_trough"] = 0.0
        return state

    def _load_state(self) -> Dict[str, Any]:
        """state.json을 로드하거나 없으면 기본 상태를 생성합니다."""
        if not self.state_file.exists():
            default_state = {
                "strategy_A": self._default_strategy_state(is_long=True),
                "strategy_B": self._default_strategy_state(is_long=False),
                "last_updated": self._get_kst_time_str()
            }
            self._save_state(default_state)
            return default_state

        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                # 누락된 키 보정
                for strat, is_l in [("strategy_A", True), ("strategy_B", False)]:
                    if strat not in data:
                        data[strat] = self._default_strategy_state(is_long=is_l)
                    else:
                        # 신규 필드 기본값 보정
                        data[strat].setdefault("trailing_mode", False)
                        data[strat].setdefault("trailing_base_price", 0.0)
                        data[strat].setdefault("trailing_target_price", 0.0)
                        data[strat].setdefault("trailing_reason", "")
                        data[strat].setdefault("trailing_triggered_at", None)
                        data[strat].setdefault("last_realized_pnl", 0.0)
                        data[strat].setdefault("last_realized_pct", 0.0)
                        data[strat].setdefault("cumulative_realized_pnl", 0.0)
                return data
        except Exception as e:
            logger.error(f"[CycleManager] state.json 로드 실패, 기본 상태로 초기화합니다: {e}")

            default_state = {
                "strategy_A": self._default_strategy_state(is_long=True),
                "strategy_B": self._default_strategy_state(is_long=False),
                "last_updated": self._get_kst_time_str()
            }
            return default_state

    def _save_state(self, state: Dict[str, Any]):
        """state.json을 임시 파일을 통해 원자적(Atomic)으로 저장합니다."""
        state["last_updated"] = self._get_kst_time_str()
        temp_file = self.state_file.with_suffix(".tmp")
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            temp_file.replace(self.state_file)
            self.state = state
        except Exception as e:
            logger.error(f"[CycleManager] state.json 저장 실패: {e}")
            if temp_file.exists():
                temp_file.unlink()

    def get_strategy_state(self, strategy_name: str) -> Dict[str, Any]:
        """특정 전략의 현재 상태 사본을 반환합니다."""
        return self.state.get(strategy_name, {}).copy()

    def update_price_extremes(self, strategy_name: str, current_price: float):
        """싸이클 내 전고점(Long) 또는 전저점(Short)을 갱신합니다."""
        s = self.state[strategy_name]
        if strategy_name == "strategy_A":
            prev_peak = s.get("cycle_peak", 0.0)
            if prev_peak <= 0.0 or current_price > prev_peak:
                s["cycle_peak"] = current_price
                self._save_state(self.state)
        elif strategy_name == "strategy_B":
            prev_trough = s.get("cycle_trough", 0.0)
            if prev_trough <= 0.0 or current_price < prev_trough:
                s["cycle_trough"] = current_price
                self._save_state(self.state)

    def determine_buy_action(self, strategy_name: str) -> Tuple[str, float, str]:
        """
        현재 더미 및 실행 유닛 상태에 따라 매수 신호 발생 시 취할 행동을 결정합니다.
        Returns:
            Tuple[action_type, weight, label]
            - action_type: 'DUMMY' or 'REAL_BUY'
            - weight: 0.0(더미), 1.0(기본 유닛), 1.25(비중 확대 유닛)
            - label: 설명 라벨 (e.g. '더미 1회차', '실제 1회차 (1.0x)', '실제 5회차 (1.25x)')
        """
        s = self.state[strategy_name]
        dummy_count = s.get("dummy_count", 0)
        executed_units = s.get("executed_units", 0)

        # 1. 더미 소진 단계 (0~1회차 -> 2회 소진 필요)
        if dummy_count < DUMMY_TARGET:
            next_dummy = dummy_count + 1
            return "DUMMY", 0.0, f"더미 {next_dummy}회차"

        # 2. 실전 매수 단계 (3회차 신호부터 실제 집행)
        current_unit_seq = executed_units + 1
        if executed_units < WEIGHTED_UNIT_THRESHOLD:
            # 1~4회차 (executed_units: 0~3) -> 1.0x
            return "REAL_BUY", WEIGHT_NORMAL, f"실제 {current_unit_seq}회차 ({WEIGHT_NORMAL:.1f}x)"
        else:
            # 5회차 이상 (executed_units >= 4) -> 1.25x
            return "REAL_BUY", WEIGHT_BOOST, f"실제 {current_unit_seq}회차 ({WEIGHT_BOOST:.2f}x)"

    def consume_dummy(self, strategy_name: str, date_str: str) -> int:
        """더미 카운트를 1 증가시키고 상태를 저장합니다."""
        s = self.state[strategy_name]
        s["dummy_count"] = min(s.get("dummy_count", 0) + 1, DUMMY_TARGET)
        s["last_action"] = "BUY_DUMMY"
        s["last_action_date"] = date_str
        self._save_state(self.state)
        logger.info(f"[CycleManager] [{strategy_name}] 더미 소진 처리: {s['dummy_count']} / {DUMMY_TARGET}")
        return s["dummy_count"]

    def record_executed_unit(self, strategy_name: str, price: float, qty: float, date_str: str):
        """실제 매수 주문 체결 후 평단가, 수량, 실행 유닛 수를 업데이트합니다."""
        s = self.state[strategy_name]
        old_qty = s.get("total_qty", 0.0)
        old_avg = s.get("avg_price", 0.0)

        new_qty = old_qty + qty
        new_avg = ((old_qty * old_avg) + (qty * price)) / new_qty if new_qty > 0 else price

        s["total_qty"] = new_qty
        s["avg_price"] = new_avg
        s["executed_units"] = s.get("executed_units", 0) + 1
        s["last_action"] = "BUY_UNIT"
        s["last_action_date"] = date_str

        # 극값 초기화/갱신
        if strategy_name == "strategy_A":
            s["cycle_peak"] = max(s.get("cycle_peak", 0.0), price)
        else:
            s["cycle_trough"] = min(s.get("cycle_trough", 999999.0), price) if s.get("cycle_trough", 0.0) > 0 else price

        self._save_state(self.state)
        logger.info(
            f"[CycleManager] [{strategy_name}] 실제 유닛 집행 기록: "
            f"누적 {s['executed_units']}회차, 신규수량 {qty:,.4f}, 총수량 {new_qty:,.4f}, 신규평단 ${new_avg:,.2f}"
        )

    def reset_cycle(self, strategy_name: str, date_str: str, transfer_to_dummy1: bool = False):
        """
        포지션 청산 완료 후 싸이클을 초기화합니다.
        당일 청산 후 매수 신호 동시 발생 시 transfer_to_dummy1=True로 호출하여 1차 더미로 이관합니다.
        """
        s = self.state[strategy_name]
        next_cycle_id = s.get("cycle_id", 1) + 1

        s["cycle_id"] = next_cycle_id
        s["executed_units"] = 0
        s["avg_price"] = 0.0
        s["total_qty"] = 0.0

        if strategy_name == "strategy_A":
            s["cycle_peak"] = 0.0
        else:
            s["cycle_trough"] = 0.0

        # 트레일링 모드 초기화
        s["trailing_mode"] = False
        s["trailing_base_price"] = 0.0
        s["trailing_target_price"] = 0.0
        s["trailing_reason"] = ""
        s["trailing_triggered_at"] = None

        if transfer_to_dummy1:
            s["dummy_count"] = 1
            s["last_action"] = "BUY_DUMMY"
            s["last_action_date"] = date_str
            logger.info(f"[CycleManager] [{strategy_name}] 싸이클 #{next_cycle_id} 시작 (당일 청산 후 매수신호 발생으로 1차 더미 이관 처리)")
        else:
            s["dummy_count"] = 0
            s["last_action"] = "SELL_CLEAR"
            s["last_action_date"] = date_str
            logger.info(f"[CycleManager] [{strategy_name}] 싸이클 #{next_cycle_id} 초기화 완료 (모든 포지션 청산)")

        self._save_state(self.state)

    def record_cycle_realized_pnl(self, strategy_name: str, pnl_usd: float, pnl_pct: float):
        """싸이클 청산 시 실현 손익 및 누적 실현 손익을 기록합니다."""
        s = self.state[strategy_name]
        s["last_realized_pnl"] = round(pnl_usd, 4)
        s["last_realized_pct"] = round(pnl_pct, 4)
        s["cumulative_realized_pnl"] = round(s.get("cumulative_realized_pnl", 0.0) + pnl_usd, 4)
        self._save_state(self.state)
        logger.info(
            f"[CycleManager] [{strategy_name}] 청산 실현손익 기록: {pnl_usd:+,.2f} USD ({pnl_pct:+.2f}%), "
            f"누적 실현손익: {s['cumulative_realized_pnl']:+,.2f} USD"
        )

    def set_trailing_mode(
        self,
        strategy_name: str,
        target_price: float,
        base_price: float,
        reason: str,
        date_str: str
    ):
        """1차 목표가 달성 후 2단계 4H 5MA 감시 모드를 활성화합니다."""
        s = self.state[strategy_name]
        s["trailing_mode"] = True
        s["trailing_target_price"] = target_price
        s["trailing_base_price"] = base_price
        s["trailing_reason"] = reason
        s["trailing_triggered_at"] = date_str
        self._save_state(self.state)
        logger.info(
            f"[CycleManager] [{strategy_name}] 2단계 트레일링 감시 활성화: "
            f"1차목표가=${target_price:,.2f}, 최소보존선=${base_price:,.2f} ({reason})"
        )

    def clear_trailing_mode(self, strategy_name: str):
        """트레일링 모드를 해제합니다."""
        s = self.state[strategy_name]
        s["trailing_mode"] = False
        s["trailing_base_price"] = 0.0
        s["trailing_target_price"] = 0.0
        s["trailing_reason"] = ""
        s["trailing_triggered_at"] = None
        self._save_state(self.state)
        logger.info(f"[CycleManager] [{strategy_name}] 2단계 트레일링 감시 해제")

    def is_any_trailing_active(self) -> bool:
        """A 또는 B 전략 중 어느 하나라도 2단계 트레일링 감시 중인지 확인합니다."""
        a_active = self.state.get("strategy_A", {}).get("trailing_mode", False)
        b_active = self.state.get("strategy_B", {}).get("trailing_mode", False)
        return bool(a_active or b_active)



cycle_manager = CycleManager()
