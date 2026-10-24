"""
manager/cycle_manager.py
A/B 전략별 더미 카운트, 실제 매수 회차(executed_units) 및 평단가/수량 영속성 관리 모듈 (PRD 섹션 2 & 5)
"""

import atexit
import hashlib
import json
import os
import shutil
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, Tuple, Optional
from config import (
    STATE_FILE, PAPER_TRADING,
    DUMMY_TARGET, WEIGHTED_UNIT_THRESHOLD, WEIGHT_NORMAL, WEIGHT_BOOST, MAX_EXECUTED_UNITS
)
from utils.logger import logger

KST = timezone(timedelta(hours=9))


# ---------------------------------------------------------------------
# 구글 시트 STATE_SYNC (봇 상태 스냅샷) 백그라운드 전송
#  - 엑셀 원장에 남지 않는 상태 변경(거래소 대조 자동 보정, 수동 채택, 트레일링 돌입, 부분 청산 잔량)을
#    대시보드가 알 수 있도록 state.json 저장 직후 핵심 필드만 보냅니다.
#  - 별도 스레드에서 보내므로 매매 루프를 절대 막지 않습니다. 모든 예외는 이 안에서 처리합니다.
#  - 직전에 보낸 내용과 같으면 보내지 않고, 연속 전송 사이에는 최소 10초 간격을 둡니다(최신값만 전송).
# ---------------------------------------------------------------------
_SYNC_FIELDS = (
    "total_qty", "avg_price", "executed_units", "dummy_count", "cycle_id",
    "trailing_mode", "trailing_base_price", "trailing_target_price",
    "cumulative_realized_pnl", "last_action", "last_action_date",
)


def _import_sheets_logger():
    try:
        from sheets_logger import SheetsLogger
        return SheetsLogger
    except ImportError:
        import sys
        here = Path(__file__).resolve().parent
        for p in (here, here.parent):
            if str(p) not in sys.path:
                sys.path.append(str(p))
        from sheets_logger import SheetsLogger
        return SheetsLogger


class _StateSyncPusher:
    MIN_INTERVAL = 10.0   # 연속 전송 최소 간격(초)
    MAX_FAILS = 3         # 연속 실패 시 이번 스냅샷은 포기(다음 상태 변경 때 다시 시도)

    def __init__(self):
        self._lock = threading.Lock()
        self._pending = None
        self._pending_body = None
        self._last_body = None
        self._last_sent = 0.0
        self._fails = 0
        self._thread = None
        self._flush_evt = threading.Event()
        self._sheet_logger = None
        self._disabled = False
        atexit.register(self.flush, 5.0)   # 종료 직전 대기 중인 스냅샷 전송 시도

    def _get_sheet_logger(self):
        if self._sheet_logger is None and not self._disabled:
            try:
                SheetsLogger = _import_sheets_logger()
                lg = SheetsLogger(strategy_name="v3-switch", exchange="Bitget", timeout=5, silent=True)
                if not lg.webhook_url or not lg.auth_token:
                    self._disabled = True
                    logger.warning("[CycleManager] GSHEET_WEBHOOK_URL/GSHEET_AUTH_TOKEN 미설정: 상태 동기화(STATE_SYNC)를 건너뜁니다.")
                    return None
                self._sheet_logger = lg
            except Exception as e:
                self._disabled = True
                logger.warning(f"[CycleManager] 시트 로거 로드 실패, 상태 동기화 비활성화: {e}")
        return self._sheet_logger

    def push(self, snapshot: Dict[str, Any]):
        if self._disabled:
            return
        try:
            body = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str)
            with self._lock:
                if body == self._last_body or body == self._pending_body:
                    return
                self._pending = snapshot
                self._pending_body = body
                if self._thread is None:
                    self._thread = threading.Thread(target=self._run, name="v3-state-sync", daemon=True)
                    self._thread.start()
        except Exception as e:
            logger.warning(f"[CycleManager] 상태 스냅샷 큐 등록 실패(무시): {e}")

    def flush(self, timeout: float = 5.0):
        t = self._thread
        if t is None:
            return
        self._flush_evt.set()
        try:
            t.join(timeout)
        except Exception:
            pass

    def _run(self):
        try:
            while True:
                with self._lock:
                    if self._pending is None:
                        self._thread = None
                        return
                    wait = self.MIN_INTERVAL - (time.time() - self._last_sent)
                if wait > 0 and not self._flush_evt.is_set():
                    self._flush_evt.wait(timeout=wait)
                    continue
                with self._lock:
                    snap, body = self._pending, self._pending_body
                    self._pending, self._pending_body = None, None
                ok = False
                try:
                    lg = self._get_sheet_logger()
                    ok = bool(lg.log_bot_state(snap, strategy_name="v3-switch")) if lg else False
                except Exception as e:
                    logger.warning(f"[CycleManager] 상태 스냅샷 전송 예외(무시): {e}")
                with self._lock:
                    self._last_sent = time.time()
                    if ok:
                        self._last_body = body
                        self._fails = 0
                    elif self._disabled:
                        self._pending, self._pending_body = None, None
                    else:
                        self._fails += 1
                        if self._fails < self.MAX_FAILS and self._pending is None:
                            self._pending, self._pending_body = snap, body   # 같은 스냅샷 재시도
                        else:
                            self._fails = 0
                            logger.warning("[CycleManager] 상태 스냅샷 전송 연속 실패: 다음 상태 변경 때 다시 시도합니다.")
        except Exception as e:
            logger.warning(f"[CycleManager] 상태 동기화 스레드 오류(무시): {e}")
            with self._lock:
                self._thread = None


_state_pusher = None


def _get_state_pusher() -> _StateSyncPusher:
    global _state_pusher
    if _state_pusher is None:
        _state_pusher = _StateSyncPusher()
    return _state_pusher


class CycleManager:
    """
    state.json 파일을 원자적으로 읽고 쓰며 사이클 및 더미 진행 상황을 제어하는 매니저
    """
    def __init__(self, state_file: Path = STATE_FILE):
        self.state_file = Path(state_file)
        self.backup_file = self.state_file.with_suffix(".bak.json")
        self.load_error: Optional[str] = None
        self.state: Dict[str, Any] = self._load_state()
        self._push_state_sync(self.state)   # 시작 시점 상태를 대시보드에 반영

    def _build_sync_snapshot(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """대시보드가 필요한 핵심 필드만 추립니다 (last_updated 등 매번 바뀌는 값은 제외)."""
        snap: Dict[str, Any] = {"schema": 1, "bot": "v3-switch"}
        for key in ("strategy_A", "strategy_B"):
            s = state.get(key, {}) or {}
            snap[key] = {f: s.get(f) for f in _SYNC_FIELDS}
        return snap

    def _push_state_sync(self, state: Dict[str, Any]):
        """state.json 저장 직후 호출. 실패해도 절대 예외를 밖으로 던지지 않습니다."""
        try:
            if self.load_error:
                return
            _get_state_pusher().push(self._build_sync_snapshot(state))
        except Exception as e:
            logger.warning(f"[CycleManager] 상태 동기화 등록 실패(무시): {e}")
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
            "trailing_activated_ts": 0,
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
                        data[strat].setdefault("trailing_activated_ts", 0)
                        data[strat].setdefault("last_realized_pnl", 0.0)
                        data[strat].setdefault("last_realized_pct", 0.0)
                        data[strat].setdefault("cumulative_realized_pnl", 0.0)
                return data
        except Exception as e:
            logger.error(f"[CycleManager] state.json 로드 실패, 기본 상태로 초기화합니다: {e}")
            backup_path = self._backup_corrupted_file()
            if not PAPER_TRADING:
                self.load_error = (
                    f"state.json 로드 실패({e}). 손상 파일 백업: {backup_path}. "
                    f"'python main.py --reconcile --adopt-exchange'로 복구하세요."
                )
                logger.critical(f"[CycleManager] {self.load_error}")
            default_state = {
                "strategy_A": self._default_strategy_state(is_long=True),
                "strategy_B": self._default_strategy_state(is_long=False),
                "last_updated": self._get_kst_time_str()
            }
            return default_state

    def _save_state(self, state: Dict[str, Any]):
        """state.json을 임시 파일을 통해 원자적(Atomic)으로 저장합니다."""
        if self.load_error:
            logger.error("[CycleManager] state.json 손상 상태이므로 저장을 차단합니다 (기존 파일 보호).")
            return
        state["last_updated"] = self._get_kst_time_str()
        temp_file = self.state_file.with_suffix(".tmp")
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            if self.state_file.exists():
                shutil.copy2(self.state_file, self.backup_file)
            temp_file.replace(self.state_file)
            self.state = state
            self._push_state_sync(state)
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
            - action_type: 'DUMMY', 'REAL_BUY' 또는 'MAX_REACHED'(최대 회차 도달, 추가 매수 중단)
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

        # 2. 최대 회차 도달 시 추가 매수 중단 (MAX_EXECUTED_UNITS)
        if executed_units >= MAX_EXECUTED_UNITS:
            return "MAX_REACHED", 0.0, f"최대 {MAX_EXECUTED_UNITS}회차 도달"

        # 3. 실전 매수 단계 (3회차 신호부터 실제 집행)
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

    def reset_incomplete_dummy(self, strategy_name: str, trigger_source: str, date_str: str) -> bool:
        """
        상대 전략의 최초 실전 매수(BUY_UNIT 1회차) 체결 시, 아직 실전 매수에 진입하지 않은 상태(executed_units == 0)의
        과거 미완성 유령 더미(dummy_count > 0)를 0/2로 초기화합니다.
        (상대 전략 2회차 이후 추가 매수 시 발생하는 반대편 헷지 더미는 보존됨)
        Returns:
            bool: 초기화가 실제로 수행되었는지 여부
        """
        s = self.state.get(strategy_name, {})
        if s.get("executed_units", 0) == 0 and s.get("dummy_count", 0) > 0:
            old_dummy = s["dummy_count"]
            s["dummy_count"] = 0
            s["last_action"] = "RESET_DUMMY_ON_OPPOSITE_BUY"
            s["last_action_date"] = date_str
            self._save_state(self.state)
            logger.info(
                f"[CycleManager] [{strategy_name}] 상대 전략({trigger_source}) 최초 실제 매수 체결로 "
                f"과거 미완성 유령 더미({old_dummy}/2) -> 0/2 초기화 완료"
            )
            return True
        return False

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
        s["trailing_activated_ts"] = 0

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
        s["trailing_activated_ts"] = int(time.time() * 1000)   # 4H 완성봉 판정 기준 시각(ms)
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
        s["trailing_activated_ts"] = 0
        self._save_state(self.state)
        logger.info(f"[CycleManager] [{strategy_name}] 2단계 트레일링 감시 해제")

    def ratchet_trailing_base(
        self,
        strategy_name: str,
        candidate_price: float,
        reason: str,
        date_str: str
    ) -> Tuple[bool, float, float]:
        """
        2단계 트레일링 중 일봉 재평가로 산출된 신규 목표가를 최소 이익 보존선에 '유리한 방향으로만' 반영합니다.
        - 롱(strategy_A): 후보가 기존보다 높을 때만 상향 (max)
        - 숏(strategy_B): 후보가 기존보다 낮을 때만 하향 (min)
        Returns: (갱신 여부, 기존 보존선, 적용 후 보존선)
        """
        s = self.state[strategy_name]
        old_base = float(s.get("trailing_base_price", 0.0) or 0.0)
        if not s.get("trailing_mode", False) or candidate_price <= 0:
            return False, old_base, old_base

        if strategy_name == "strategy_A":
            improved = old_base <= 0 or candidate_price > old_base
        else:
            improved = old_base <= 0 or candidate_price < old_base
        if not improved:
            return False, old_base, old_base

        s["trailing_base_price"] = float(candidate_price)
        self._save_state(self.state)
        logger.info(
            f"[CycleManager] [{strategy_name}] 최소 이익 보존선 래칫 갱신: "
            f"${old_base:,.2f} → ${candidate_price:,.2f} ({reason}, {date_str})"
        )
        return True, old_base, float(candidate_price)

    def is_any_trailing_active(self) -> bool:
        """A 또는 B 전략 중 어느 하나라도 2단계 트레일링 감시 중인지 확인합니다."""
        a_active = self.state.get("strategy_A", {}).get("trailing_mode", False)
        b_active = self.state.get("strategy_B", {}).get("trailing_mode", False)
        return bool(a_active or b_active)

    # ------------------------------------------------------------------
    # 손상 대응 / 포지션 대조 지원 메서드
    # ------------------------------------------------------------------
    def _build_default_state(self) -> Dict[str, Any]:
        """A/B 전략 기본 상태 딕셔너리를 생성합니다."""
        return {
            "strategy_A": self._default_strategy_state(is_long=True),
            "strategy_B": self._default_strategy_state(is_long=False),
            "last_updated": self._get_kst_time_str()
        }

    def _backup_corrupted_file(self) -> Optional[Path]:
        """손상된 state.json을 타임스탬프 사본(state.corrupt_YYYYMMDD_HHMMSS.json)으로 보관합니다."""
        try:
            stamp = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
            backup_path = self.state_file.with_name(f"{self.state_file.stem}.corrupt_{stamp}.json")
            shutil.copy2(self.state_file, backup_path)
            logger.warning(f"[CycleManager] 손상된 state 파일 백업 완료: {backup_path.name}")
            return backup_path
        except Exception as e:
            logger.error(f"[CycleManager] 손상 state 파일 백업 실패: {e}")
            return None

    def reload(self):
        """디스크의 state.json을 다시 읽어 메모리 상태를 갱신합니다. (--loop 모드 틱마다 호출)"""
        self.load_error = None
        self.state = self._load_state()

    def recover_with_default_state(self):
        """손상 상태에서 기본 상태로 재생성합니다. (--reconcile --adopt-exchange 수동 복구 전용)"""
        self.load_error = None
        self._save_state(self._build_default_state())
        logger.warning("[CycleManager] 손상된 state.json을 기본 상태로 재생성했습니다 (거래소 포지션 채택 예정).")

    def sync_position_values(self, strategy_name: str, qty: float, avg_price: float):
        """포지션 대조 결과(허용 오차 이내)를 반영해 수량/평단을 거래소 값으로 보정합니다."""
        s = self.state[strategy_name]
        old_qty, old_avg = s.get("total_qty", 0.0), s.get("avg_price", 0.0)
        s["total_qty"] = float(qty)
        if avg_price > 0:
            s["avg_price"] = float(avg_price)
        self._save_state(self.state)
        logger.info(
            f"[CycleManager] [{strategy_name}] 거래소 기준 자동 보정: "
            f"수량 {old_qty:,.4f} → {s['total_qty']:,.4f}, 평단 ${old_avg:,.4f} → ${s['avg_price']:,.4f}"
        )

    def adopt_exchange_position(self, strategy_name: str, qty: float, avg_price: float, date_str: str):
        """거래소 포지션을 state에 강제 채택합니다. (수동 복구 전용, 더미 완료·최소 1회차로 간주)"""
        s = self.state[strategy_name]
        s["total_qty"] = float(qty)
        s["avg_price"] = float(avg_price)
        s["dummy_count"] = DUMMY_TARGET
        s["executed_units"] = max(int(s.get("executed_units", 0)), 1)
        s["last_action"] = "SYNC_ADOPT"
        s["last_action_date"] = date_str
        self._save_state(self.state)
        logger.warning(
            f"[CycleManager] [{strategy_name}] 거래소 포지션 채택: {qty:,.4f} Qty @ ${avg_price:,.4f} "
            f"(executed_units={s['executed_units']})"
        )

    def reduce_position(self, strategy_name: str, qty: float, date_str: str):
        """부분 청산 체결 수량만큼 보유 수량을 차감합니다. (평단 유지)"""
        s = self.state[strategy_name]
        s["total_qty"] = max(0.0, float(s.get("total_qty", 0.0)) - float(qty))
        s["last_action"] = "SELL_PARTIAL"
        s["last_action_date"] = date_str
        self._save_state(self.state)
        logger.warning(f"[CycleManager] [{strategy_name}] 부분 청산 반영: 잔여 수량 {s['total_qty']:,.4f}")



cycle_manager = CycleManager()
