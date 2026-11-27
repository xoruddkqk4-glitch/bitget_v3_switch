"""
backtest/entry_rule_backtest.py
진입 기준 비교 백테스트 (2단계): 현행(방향필터 + 캔들 OR 전일비) vs 전일비 단일 vs 캔들 단일

- 데이터: SOXL 미국 현물 일봉 (yfinance, auto_adjust=False → 분할 조정 OHLC, 배당 미조정)
- 판정: 매 거래일 종가 시점 평가 (실전의 15:55 ET 현재가 ≈ 종가로 근사)
- 청산: 실전 전략 클래스의 check_stage1_exit_signal 을 그대로 사용 (60일선/낙폭/하이브리드/본전가드 동일)
- 2단계 트레일링 근사 (모든 변형에 동일 적용, 상대 비교 목적):
    · ENTER_STAGE2 시 STAGE1_PARTIAL_EXIT_PCT(50%) 종가 선익절, 잔량 보존선=목표가
    · 다음 거래일부터 일봉 고/저가로 보존선(±0.2%) 터치 시 보존선 가격 청산,
      또는 종가가 일봉 SMA5 ±STAGE2_MA_BUFFER_PCT 이탈 시 종가 청산 (실전은 1H 5MA → 실전이 더 빨리 청산됨)
    · 감시 중 일봉 ENTER_STAGE2 목표가가 유리하면 보존선 래칫
- 자금: 전략별 고정 1 Unit = INITIAL_CAPITAL / UNIT_DIVISOR (복리·헤지 유닛 미반영), 수수료 TAKER_FEE_RATE 양방향
- 더미 2회 → 실전 1~4회 1.0x, 5~13회 1.25x / 당일 청산 + 신호 → 1차 더미 이관 / 상대 최초 실매수 시 미완성 더미 초기화

사용법:
    python backtest/entry_rule_backtest.py [--start 2015-01-01] [--end 2026-11-26] [--csv]
"""

import argparse
import os
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (  # noqa: E402
    UNIT_DIVISOR, MAX_EXECUTED_UNITS, DUMMY_TARGET, WEIGHTED_UNIT_THRESHOLD, WEIGHT_NORMAL, WEIGHT_BOOST,
    TAKER_FEE_RATE, STAGE1_PARTIAL_EXIT_RATIO, PRESERVATION_BUFFER_PCT, STAGE2_MA_BUFFER_PCT,
    LONG_BEARISH_CANDLE_PCT, LONG_DAILY_DROP_PCT, SHORT_BULLISH_CANDLE_PCT, SHORT_DAILY_PUMP_PCT,
)
from strategy.indicator import IndicatorCalculator  # noqa: E402
from strategy.strategy_a_long import StrategyALong  # noqa: E402
from strategy.strategy_b_short import StrategyBShort  # noqa: E402
from utils.logger import logger as _bot_logger  # noqa: E402

_bot_logger.setLevel("WARNING")   # 지표 산출 INFO 로그 억제 (일자별 수천 줄)

INITIAL_CAPITAL = 10_000.0
UNIT_USD = INITIAL_CAPITAL / UNIT_DIVISOR
WINDOW = 120


# ------------------------------------------------------------------------------
# 진입 규칙 변형
# ------------------------------------------------------------------------------
def rule_current(side: str, c: float, d: float) -> bool:
    """현행: 방향 필터(전일비 부호) AND (캔들 ±1.5% OR 전일비 ±3%)"""
    if side == "L":
        return d < 0 and (c <= LONG_BEARISH_CANDLE_PCT or d <= LONG_DAILY_DROP_PCT)
    return d > 0 and (c >= SHORT_BULLISH_CANDLE_PCT or d >= SHORT_DAILY_PUMP_PCT)


def rule_no_filter(side: str, c: float, d: float) -> bool:
    """참고: 방향 필터 없이 캔들 ±1.5% OR 전일비 ±3%"""
    if side == "L":
        return c <= LONG_BEARISH_CANDLE_PCT or d <= LONG_DAILY_DROP_PCT
    return c >= SHORT_BULLISH_CANDLE_PCT or d >= SHORT_DAILY_PUMP_PCT


def make_daily_only(th: float) -> Callable[[str, float, float], bool]:
    def _rule(side: str, c: float, d: float) -> bool:
        return d <= -th if side == "L" else d >= th
    return _rule


def make_candle_only(th: float) -> Callable[[str, float, float], bool]:
    def _rule(side: str, c: float, d: float) -> bool:
        return c <= -th if side == "L" else c >= th
    return _rule


VARIANTS: Dict[str, Callable[[str, float, float], bool]] = {
    "1_현행(필터+캔들/전일비)": rule_current,
    "2a_전일비±1.5%": make_daily_only(0.015),
    "2b_전일비±2.0%": make_daily_only(0.020),
    "2c_전일비±3.0%": make_daily_only(0.030),
    "3_캔들±1.5%": make_candle_only(0.015),
    "참고_필터없음": rule_no_filter,
}


# ------------------------------------------------------------------------------
# 상태 및 시뮬레이터
# ------------------------------------------------------------------------------
@dataclass
class SideState:
    side: str                      # "L" | "S"
    dummy_count: int = 0
    executed_units: int = 0
    total_qty: float = 0.0
    avg_price: float = 0.0
    cost_usd: float = 0.0
    cycle_peak: float = 0.0
    cycle_trough: float = 0.0
    trailing: bool = False
    base_price: float = 0.0
    trailing_from_idx: int = -1
    cycle_realized: float = 0.0
    cycle_max_units: int = 0
    # 통계
    realized: float = 0.0
    fees: float = 0.0
    cycles: int = 0
    wins: int = 0
    max_units_hits: int = 0
    unit_sum: int = 0
    entries: int = 0
    dummies: int = 0
    worst_unreal: float = 0.0
    max_deployed: float = 0.0
    exit_types: Dict[str, int] = field(default_factory=dict)

    def as_cycle_info(self) -> dict:
        return {"total_qty": self.total_qty, "executed_units": self.executed_units,
                "dummy_count": self.dummy_count, "avg_price": self.avg_price}

    def pnl(self, price: float, qty: float) -> float:
        d = 1.0 if self.side == "L" else -1.0
        return (price - self.avg_price) * qty * d

    def close_qty(self, price: float, qty: float, tag: str):
        fee = price * qty * TAKER_FEE_RATE
        p = self.pnl(price, qty) - fee
        self.realized += p
        self.cycle_realized += p
        self.fees += fee
        self.total_qty -= qty
        self.cost_usd = self.avg_price * self.total_qty
        self.exit_types[tag] = self.exit_types.get(tag, 0) + 1

    def reset_cycle(self, completed: bool):
        if completed and self.cycle_max_units > 0:
            self.cycles += 1
            self.unit_sum += self.cycle_max_units
            if self.cycle_realized > 0:
                self.wins += 1
        self.dummy_count = 0
        self.executed_units = 0
        self.total_qty = 0.0
        self.avg_price = 0.0
        self.cost_usd = 0.0
        self.trailing = False
        self.base_price = 0.0
        self.cycle_realized = 0.0
        self.cycle_max_units = 0
        if self.side == "L":
            self.cycle_peak = 0.0
        else:
            self.cycle_trough = 0.0


def simulate(df: pd.DataFrame, base: List[dict], start_idx: int,
             rule: Callable[[str, float, float], bool]) -> Tuple[SideState, SideState, pd.Series]:
    strategies = {"L": StrategyALong(), "S": StrategyBShort()}
    st = {"L": SideState("L"), "S": SideState("S")}
    equity = []

    for i in range(start_idx, len(df)):
        row = df.iloc[i]
        o, h, l, c = float(row.open), float(row.high), float(row.low), float(row.close)
        ind = dict(base[i])

        # (A) 장중 2단계 트레일링 감시 (전일 이전에 돌입한 경우만)
        for side, s in st.items():
            if not (s.trailing and s.total_qty > 0 and s.trailing_from_idx < i):
                continue
            sma5 = ind["sma5"]
            if side == "L":
                floor = s.base_price * (1.0 - PRESERVATION_BUFFER_PCT)
                if l <= floor:
                    s.close_qty(min(o, floor), s.total_qty, "2단계_보존선")
                elif c < sma5 * (1.0 - STAGE2_MA_BUFFER_PCT):
                    s.close_qty(c, s.total_qty, "2단계_5MA이탈")
            else:
                ceil = s.base_price * (1.0 + PRESERVATION_BUFFER_PCT)
                if h >= ceil:
                    s.close_qty(max(o, ceil), s.total_qty, "2단계_보존선")
                elif c > sma5 * (1.0 + STAGE2_MA_BUFFER_PCT):
                    s.close_qty(c, s.total_qty, "2단계_5MA이탈")
            if s.total_qty <= 1e-12:
                s.reset_cycle(completed=True)

        # (B) 일봉 마감 평가: 극값 갱신
        L, S = st["L"], st["S"]
        if L.cycle_peak <= 0 or c > L.cycle_peak:
            L.cycle_peak = c
        if S.cycle_trough <= 0 or c < S.cycle_trough:
            S.cycle_trough = c
        eff_peak = max(L.cycle_peak, c)
        eff_trough = min(S.cycle_trough, c)

        cleared = {"L": False, "S": False}
        # (C) 1차 청산 평가 (매도 우선)
        for side, s in st.items():
            ind_s = dict(ind)
            ind_s["drawdown_rate"] = (c - eff_peak) / eff_peak if eff_peak > 0 else 0.0
            ind_s["rebound_rate"] = (c - eff_trough) / eff_trough if eff_trough > 0 else 0.0
            if s.total_qty <= 0 and s.executed_units <= 0 and s.dummy_count <= 0:
                continue
            met, action, _reason, target = strategies[side].check_stage1_exit_signal(
                current_price=c, indicators=ind_s, cycle_info=s.as_cycle_info(), pnl_price=c)
            if s.trailing:
                if met and action == "ENTER_STAGE2" and target > 0:
                    if (side == "L" and target > s.base_price) or (side == "S" and target < s.base_price):
                        s.base_price = target
                continue
            if not met:
                continue
            if action == "CLEAR_DUMMY":
                s.reset_cycle(completed=False)
                cleared[side] = True
            elif action == "IMMEDIATE_EXIT" and s.total_qty > 0:
                s.close_qty(c, s.total_qty, "즉시본탈")
                s.reset_cycle(completed=True)
                cleared[side] = True
            elif action == "ENTER_STAGE2" and s.total_qty > 0:
                part = s.total_qty * STAGE1_PARTIAL_EXIT_RATIO
                if part > 0:
                    s.close_qty(c, part, "1단계_선익절")
                if s.total_qty <= 1e-12:
                    s.reset_cycle(completed=True)
                    cleared[side] = True
                else:
                    s.trailing = True
                    s.base_price = target
                    s.trailing_from_idx = i

        # (D) 진입 평가
        cc, dc = ind["candle_change"], ind["daily_change"]
        for side, s in st.items():
            if s.trailing:
                continue
            if not rule(side, cc, dc):
                continue
            if cleared[side]:
                s.dummy_count = 1
                s.dummies += 1
                continue
            if s.dummy_count < DUMMY_TARGET:
                s.dummy_count += 1
                s.dummies += 1
                continue
            if s.executed_units >= MAX_EXECUTED_UNITS:
                continue
            w = WEIGHT_NORMAL if s.executed_units < WEIGHTED_UNIT_THRESHOLD else WEIGHT_BOOST
            usd = UNIT_USD * w
            qty = usd / c
            fee = usd * TAKER_FEE_RATE
            s.fees += fee
            s.realized -= fee
            s.cycle_realized -= fee
            new_qty = s.total_qty + qty
            s.avg_price = (s.avg_price * s.total_qty + c * qty) / new_qty
            s.total_qty = new_qty
            s.cost_usd = s.avg_price * s.total_qty
            first_buy = s.executed_units == 0
            s.executed_units += 1
            s.entries += 1
            s.cycle_max_units = max(s.cycle_max_units, s.executed_units)
            if s.executed_units == MAX_EXECUTED_UNITS:
                s.max_units_hits += 1
            if side == "L":
                s.cycle_peak = max(s.cycle_peak, c)
            else:
                s.cycle_trough = min(s.cycle_trough, c) if s.cycle_trough > 0 else c
            # 상대 전략 최초 실매수 → 상대편 미완성 더미 초기화
            other = st["S" if side == "L" else "L"]
            if first_buy and other.executed_units == 0 and other.dummy_count > 0:
                other.dummy_count = 0

        # (E) 평가
        eq = 0.0
        for s in st.values():
            unreal = s.pnl(c, s.total_qty) if s.total_qty > 0 else 0.0
            s.worst_unreal = min(s.worst_unreal, unreal)
            s.max_deployed = max(s.max_deployed, s.cost_usd)
            eq += s.realized + unreal
        equity.append(eq)

    return st["L"], st["S"], pd.Series(equity, index=df["datetime"].iloc[start_idx:].values)


# ------------------------------------------------------------------------------
# 데이터 / 지표 / 리포트
# ------------------------------------------------------------------------------
def load_soxl() -> pd.DataFrame:
    import yfinance
    raw = yfinance.download("SOXL", period="max", progress=False, auto_adjust=False, threads=False)
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    df = pd.DataFrame({
        "open": raw["Open"].astype(float), "high": raw["High"].astype(float),
        "low": raw["Low"].astype(float), "close": raw["Close"].astype(float),
        "volume": raw["Volume"].astype(float),
    }).dropna()
    df = df[(df["open"] > 0) & (df["close"] > 0)]
    df["datetime"] = pd.to_datetime(df.index).strftime("%Y-%m-%d")
    return df.reset_index(drop=True)


def precompute(df: pd.DataFrame) -> List[Optional[dict]]:
    out: List[Optional[dict]] = [None] * len(df)
    for i in range(WINDOW - 1, len(df)):
        w = df.iloc[i - WINDOW + 1:i + 1]
        out[i] = IndicatorCalculator.calculate(w, mode="us_intraday_close")
    return out


def summarize(name: str, L: SideState, S: SideState, eq: pd.Series, last_close: float) -> dict:
    def _side(s: SideState) -> dict:
        open_pnl = s.pnl(last_close, s.total_qty) if s.total_qty > 0 else 0.0
        return {
            "실현": s.realized, "미실현(말일)": open_pnl, "싸이클": s.cycles,
            "승률%": (s.wins / s.cycles * 100) if s.cycles else 0.0,
            "실매수": s.entries, "13차도달": s.max_units_hits,
            "평균최대차수": (s.unit_sum / s.cycles) if s.cycles else 0.0,
            "최악미실현": s.worst_unreal, "최대투입": s.max_deployed,
        }
    l, sh = _side(L), _side(S)
    peak = eq.cummax()
    mdd = (eq - peak).min()
    return {
        "변형": name,
        "총손익": l["실현"] + sh["실현"] + l["미실현(말일)"] + sh["미실현(말일)"],
        "롱실현": l["실현"], "숏실현": sh["실현"],
        "말일미실현": l["미실현(말일)"] + sh["미실현(말일)"],
        "MDD($)": mdd,
        "롱싸이클": l["싸이클"], "롱승률%": l["승률%"], "롱13차": l["13차도달"], "롱최악미실현": l["최악미실현"],
        "숏싸이클": sh["싸이클"], "숏승률%": sh["승률%"], "숏13차": sh["13차도달"], "숏최악미실현": sh["최악미실현"],
        "실매수(L/S)": f"{l['실매수']}/{sh['실매수']}",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2015-01-01")
    ap.add_argument("--end", default="")
    ap.add_argument("--csv", action="store_true", help="결과를 backtest/results_entry_rule.csv 로 저장")
    args = ap.parse_args()

    df = load_soxl()
    if args.end:
        df = df[df["datetime"] <= args.end].reset_index(drop=True)
    base = precompute(df)
    start_idx = max(WINDOW - 1, int((df["datetime"] >= args.start).idxmax()))
    print(f"[Backtest] SOXL {df['datetime'].iloc[start_idx]} ~ {df['datetime'].iloc[-1]} "
          f"({len(df) - start_idx}거래일), 1 Unit=${UNIT_USD:,.2f}, 수수료 {TAKER_FEE_RATE*100:.2f}%")

    # 갭 분리일 통계
    seg = [b for b in base[start_idx:] if b]
    split_days = sum(1 for b in seg if (b["candle_change"] > 0) != (b["daily_change"] > 0) and abs(b["candle_change"]) >= 0.015)
    print(f"[Backtest] 캔들/전일비 부호 불일치(|캔들|≥1.5%) 일수: {split_days} / {len(seg)} ({split_days/len(seg)*100:.1f}%)\n")

    rows = []
    yearly = {}
    last_close = float(df["close"].iloc[-1])
    for name, rule in VARIANTS.items():
        L, S, eq = simulate(df, base, start_idx, rule)
        rows.append(summarize(name, L, S, eq, last_close))
        years = pd.to_datetime(pd.Series(eq.index)).dt.year.values
        eq_y = pd.Series(eq.values, index=years)
        yearly[name] = eq_y.groupby(level=0).last().diff().fillna(eq_y.groupby(level=0).last())
        print(f"  - {name}: 롱 청산유형 {L.exit_types} / 숏 청산유형 {S.exit_types}")

    res = pd.DataFrame(rows).set_index("변형")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    pd.set_option("display.float_format", lambda v: f"{v:,.1f}")
    print()
    print(res.to_string())
    print("\n[연도별 손익 변화($, 실현+미실현)]")
    print(pd.DataFrame(yearly).to_string())
    if args.csv:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results_entry_rule.csv")
        res.to_csv(path, encoding="utf-8-sig")
        print(f"\n[Backtest] 저장: {path}")


if __name__ == "__main__":
    main()
