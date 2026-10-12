"""
manager/reconciler.py
state.json ↔ 거래소 실제 포지션 대조(Reconciliation) 모듈
- 실전 모드에서만 거래소를 조회하며, 불일치(MISMATCH) 전략은 매매를 차단합니다.
- 허용 오차 이내 차이는 RECONCILE_AUTO_SYNC=True일 때 거래소 값으로 자동 보정합니다.
"""

from dataclasses import dataclass
from typing import Dict

from config import RECONCILE_TOLERANCE_PCT, RECONCILE_AUTO_SYNC
from api.bitget_client import BitgetClient, bitget_client
from manager.cycle_manager import CycleManager, cycle_manager
from utils.excel_logger import ExcelLogger, excel_logger
from utils.logger import logger

STRATEGY_POS_SIDE = {"strategy_A": "long", "strategy_B": "short"}


@dataclass
class ReconcileResult:
    strategy: str
    pos_side: str
    state_qty: float
    exchange_qty: float
    state_avg: float
    exchange_avg: float
    status: str      # OK / SYNCED / MISMATCH / SKIPPED_PAPER
    detail: str

    @property
    def blocked(self) -> bool:
        """매매 차단 대상 여부"""
        return self.status == "MISMATCH"


class PositionReconciler:
    """state.json과 거래소 포지션을 비교·보정하는 대조기"""

    def __init__(
        self,
        client: BitgetClient = bitget_client,
        cycles: CycleManager = cycle_manager,
        excel: ExcelLogger = excel_logger
    ):
        self.client = client
        self.cycles = cycles
        self.excel = excel

    def _fetch_exchange_positions(self) -> Dict[str, Dict[str, float]]:
        long_pos, short_pos = self.client.fetch_open_positions()  # 실전 실패 시 DataUnavailableError
        return {"strategy_A": long_pos, "strategy_B": short_pos}

    def reconcile(self, auto_sync: bool = RECONCILE_AUTO_SYNC) -> Dict[str, ReconcileResult]:
        """전략별 대조 결과를 반환합니다. (모의 모드는 SKIPPED_PAPER)"""
        results: Dict[str, ReconcileResult] = {}

        if self.client.paper_trading:
            for name, side in STRATEGY_POS_SIDE.items():
                st = self.cycles.get_strategy_state(name)
                qty = float(st.get("total_qty", 0.0))
                avg = float(st.get("avg_price", 0.0))
                results[name] = ReconcileResult(name, side, qty, qty, avg, avg, "SKIPPED_PAPER", "모의 매매 모드 - 대조 생략")
            return results

        positions = self._fetch_exchange_positions()
        min_amount = self.client.get_min_amount()

        for name, side in STRATEGY_POS_SIDE.items():
            st = self.cycles.get_strategy_state(name)
            s_qty = float(st.get("total_qty", 0.0))
            s_avg = float(st.get("avg_price", 0.0))
            pos = positions[name]
            e_qty = float(pos.get("contracts", 0.0))
            e_avg = float(pos.get("entryPrice", 0.0))
            tol = max(min_amount, s_qty * RECONCILE_TOLERANCE_PCT)
            diff = abs(s_qty - e_qty)

            if s_qty <= 0 and e_qty <= 0:
                status, detail = "OK", "양쪽 모두 미보유"
            elif s_qty > 0 and e_qty <= 0:
                status, detail = "MISMATCH", f"state {s_qty:,.4f} 보유 / 거래소 0 (수동 청산·강제 청산 의심)"
            elif s_qty <= 0 and e_qty > 0:
                status, detail = "MISMATCH", f"state 0 / 거래소 {e_qty:,.4f} 보유 (수동 진입·미기록 체결 의심)"
            elif diff <= tol:
                avg_diff = e_avg > 0 and abs(s_avg - e_avg) > 1e-9
                if auto_sync and (diff > 0 or avg_diff):
                    self.cycles.sync_position_values(name, e_qty, e_avg if e_avg > 0 else s_avg)
                    status, detail = "SYNCED", f"허용 오차 이내 자동 보정 (수량 차 {diff:,.6f}, 평단 ${s_avg:,.4f}→${e_avg:,.4f})"
                else:
                    status, detail = "OK", f"일치 (수량 차 {diff:,.6f} ≤ 허용 {tol:,.6f})"
            else:
                status, detail = "MISMATCH", f"수량 차이 {diff:,.4f} > 허용 {tol:,.4f} (state {s_qty:,.4f} / 거래소 {e_qty:,.4f})"

            log_fn = logger.warning if status == "MISMATCH" else logger.info
            log_fn(f"[Reconciler] [{name}] {status}: {detail}")
            results[name] = ReconcileResult(name, side, s_qty, e_qty, s_avg, e_avg, status, detail)

        return results

    def adopt_exchange(self, date_str: str) -> Dict[str, ReconcileResult]:
        """거래소 포지션 값을 state에 강제 채택합니다. (python main.py --reconcile --adopt-exchange 전용)"""
        if self.cycles.load_error:
            self.cycles.recover_with_default_state()

        positions = self._fetch_exchange_positions()
        results: Dict[str, ReconcileResult] = {}

        for name, side in STRATEGY_POS_SIDE.items():
            st = self.cycles.get_strategy_state(name)
            s_qty = float(st.get("total_qty", 0.0))
            s_avg = float(st.get("avg_price", 0.0))
            pos = positions[name]
            e_qty = float(pos.get("contracts", 0.0))
            e_avg = float(pos.get("entryPrice", 0.0))

            if e_qty <= 0 and s_qty > 0:
                self.cycles.reset_cycle(name, date_str=date_str, transfer_to_dummy1=False)
                self.excel.append_record(
                    date_str=date_str, strategy_name=name, event_type="SYNC_RESET",
                    price=None, qty=None, unit_label="SYNC", dummy_status="0 / 2", total_balance=0.0,
                    note=f"거래소 포지션 0 채택: state {s_qty:,.4f} Qty → 싸이클 초기화 (수동 복구)",
                    auto_update_chart=False
                )
                status, detail = "SYNCED", "거래소 0 채택 → 싸이클 초기화"
            elif e_qty > 0:
                self.cycles.adopt_exchange_position(name, e_qty, e_avg, date_str)
                self.excel.append_record(
                    date_str=date_str, strategy_name=name, event_type="SYNC_ADOPT",
                    price=e_avg, qty=e_qty, unit_label="SYNC", dummy_status="2 / 2", total_balance=0.0,
                    note=f"거래소 포지션 채택: {e_qty:,.4f} Qty @ ${e_avg:,.2f} (기존 state {s_qty:,.4f} @ ${s_avg:,.2f})",
                    auto_update_chart=False
                )
                status, detail = "SYNCED", f"거래소 {e_qty:,.4f} @ ${e_avg:,.2f} 채택"
            else:
                status, detail = "OK", "양쪽 모두 미보유"

            logger.warning(f"[Reconciler] [{name}] 수동 채택 결과: {status} - {detail}")
            results[name] = ReconcileResult(name, side, s_qty, e_qty, s_avg, e_avg, status, detail)

        return results


reconciler = PositionReconciler()
