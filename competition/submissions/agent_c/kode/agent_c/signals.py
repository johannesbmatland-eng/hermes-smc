"""A+ setup checklist — ALL filters must pass; default = no trade."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

import numpy as np
import pandas as pd

from .config import CostModel, StrategyParams, DEFAULT_COSTS, DEFAULT_PARAMS


class Side(IntEnum):
    FLAT = 0
    LONG = 1
    SHORT = -1


@dataclass
class FilterResult:
    side: Side
    atr_expand: bool
    range_break: bool
    false_break_ok: bool
    flow_ok: bool
    regime_ok: bool
    tod_ok: bool
    shock_ok: bool
    fee_hurdle_ok: bool
    reasons: tuple[str, ...]

    @property
    def a_plus(self) -> bool:
        return (
            self.side != Side.FLAT
            and self.atr_expand
            and self.range_break
            and self.false_break_ok
            and self.flow_ok
            and self.regime_ok
            and self.tod_ok
            and self.shock_ok
            and self.fee_hurdle_ok
        )


def _fee_hurdle_ok(p: StrategyParams, costs: CostModel, atr: float, price: float) -> bool:
    """Expectancy in price terms must clear min_edge * round-trip cost on notional.

    Approximate per-unit expectancy using prior hit-rate and R-multiples:
      E[R] = hr * avg_win_R - (1-hr) * avg_loss_R
      E[$/unit] ≈ E[R] * stop_distance
    Compare to RT cost * price.
    """
    if not np.isfinite(atr) or atr <= 0 or price <= 0:
        return False
    stop = p.stop_atr_mult * atr
    e_R = p.prior_hit_rate * p.prior_avg_win_R - (1.0 - p.prior_hit_rate) * p.prior_avg_loss_R
    if e_R <= 0:
        return False
    e_price = e_R * stop
    rt_cost_price = costs.round_trip_frac * price
    return e_price >= p.min_edge_multiple_of_rt_cost * rt_cost_price


def evaluate_bar(
    i: int,
    feat: pd.DataFrame,
    params: StrategyParams | None = None,
    costs: CostModel | None = None,
) -> FilterResult:
    """Evaluate A+ checklist at bar i using only data <= i (no lookahead on confirm).

    False-break confirmation uses the last `confirm_bars` closes vs prior Donchian.
    Signal is actionable on the *next* bar open in the backtester.
    """
    p = params or DEFAULT_PARAMS
    costs = costs or DEFAULT_COSTS
    reasons: list[str] = []

    need = max(
        p.atr_baseline_len,
        p.range_lookback,
        p.vol_sma_len,
        p.ema_slow,
        p.er_len,
        p.confirm_bars + 2,
        p.flow_lookback * 8,
    )
    if i < need:
        return FilterResult(
            Side.FLAT, False, False, False, False, False, False, False, False, ("warmup",)
        )

    row = feat.iloc[i]
    atr = float(row["atr"])
    atr_ratio = float(row["atr_ratio"])
    up = float(row["donch_up"])
    dn = float(row["donch_dn"])
    close = float(row["close"])
    er = float(row["er"])
    flow_z = float(row["flow_z"])
    vol_ratio = float(row["vol_ratio"])
    ema_f = float(row["ema_fast"])
    ema_s = float(row["ema_slow"])
    hour = int(row["hour"])
    dow = int(row["dow"])
    ret1 = float(row["ret1"])

    # 1) ATR expansion (vol breakout regime)
    atr_expand = np.isfinite(atr_ratio) and atr_ratio >= p.atr_expand_min
    if not atr_expand:
        reasons.append("no_atr_expand")

    # 2) Range break with buffer
    buf = p.break_buffer_atr * atr if np.isfinite(atr) else np.inf
    long_break = np.isfinite(up) and close > up + buf
    short_break = np.isfinite(dn) and close < dn - buf
    range_break = long_break or short_break
    if not range_break:
        reasons.append("no_range_break")

    side = Side.FLAT
    if long_break and not short_break:
        side = Side.LONG
    elif short_break and not long_break:
        side = Side.SHORT
    elif long_break and short_break:
        # pathological; skip
        range_break = False
        reasons.append("both_sides")

    # 3) False-break filter: last confirm_bars closes must stay outside range
    false_break_ok = False
    if side == Side.LONG:
        closes = feat["close"].iloc[i - p.confirm_bars + 1 : i + 1].to_numpy(dtype=np.float64)
        # all closes above prior up (without requiring buffer on early confirm bars)
        false_break_ok = bool(np.all(closes > up))
        # invalidate if any low deeply re-enters range
        lows = feat["low"].iloc[i - p.confirm_bars + 1 : i + 1].to_numpy(dtype=np.float64)
        if np.any(lows < up - p.false_break_reentry_atr * atr):
            false_break_ok = False
            reasons.append("false_break_reentry")
        if not false_break_ok and "false_break_reentry" not in reasons:
            reasons.append("false_break_no_hold")
    elif side == Side.SHORT:
        closes = feat["close"].iloc[i - p.confirm_bars + 1 : i + 1].to_numpy(dtype=np.float64)
        false_break_ok = bool(np.all(closes < dn))
        highs = feat["high"].iloc[i - p.confirm_bars + 1 : i + 1].to_numpy(dtype=np.float64)
        if np.any(highs > dn + p.false_break_reentry_atr * atr):
            false_break_ok = False
            reasons.append("false_break_reentry")
        if not false_break_ok and "false_break_reentry" not in reasons:
            reasons.append("false_break_no_hold")
    else:
        reasons.append("no_side")

    # 4) Flow proxy aligned with break direction
    flow_ok = (
        np.isfinite(vol_ratio)
        and vol_ratio >= p.vol_surge_min
        and np.isfinite(flow_z)
        and (
            (side == Side.LONG and flow_z >= p.flow_z_min)
            or (side == Side.SHORT and flow_z <= -p.flow_z_min)
        )
    )
    if not flow_ok:
        reasons.append("flow_fail")

    # 5) Regime: efficiency + EMA alignment
    regime_ok = np.isfinite(er) and er >= p.er_min
    if p.require_ema_align and side != Side.FLAT:
        if not (np.isfinite(ema_f) and np.isfinite(ema_s)):
            regime_ok = False
        elif side == Side.LONG and not (ema_f > ema_s and close > ema_f):
            regime_ok = False
        elif side == Side.SHORT and not (ema_f < ema_s and close < ema_f):
            regime_ok = False
    if not regime_ok:
        reasons.append("regime_fail")

    # 6) Time-of-day / DOW
    tod_ok = hour in p.allowed_hours_utc and dow not in p.blocked_dow
    if not tod_ok:
        reasons.append("tod_block")

    # 7) Shock skip
    shock_ok = np.isfinite(ret1) and abs(ret1) < p.shock_ret_abs
    if not shock_ok:
        reasons.append("shock")

    # 8) Fee / expectancy hurdle
    fee_hurdle_ok = _fee_hurdle_ok(p, costs, atr, close)
    if not fee_hurdle_ok:
        reasons.append("fee_hurdle")

    # Independent core filters for A+ (≥4): atr_expand, range_break+false_break,
    # flow_ok, regime_ok — plus tod, shock, fee as gates.
    return FilterResult(
        side=side if range_break else Side.FLAT,
        atr_expand=atr_expand,
        range_break=range_break,
        false_break_ok=false_break_ok,
        flow_ok=flow_ok,
        regime_ok=regime_ok,
        tod_ok=tod_ok,
        shock_ok=shock_ok,
        fee_hurdle_ok=fee_hurdle_ok,
        reasons=tuple(reasons),
    )


def generate_signals(feat: pd.DataFrame, params: StrategyParams | None = None, costs: CostModel | None = None) -> pd.DataFrame:
    """Return dataframe with signal columns; signal at i means enter next bar open."""
    p = params or DEFAULT_PARAMS
    costs = costs or DEFAULT_COSTS
    n = len(feat)
    side = np.zeros(n, dtype=np.int8)
    a_plus = np.zeros(n, dtype=bool)
    for i in range(n):
        fr = evaluate_bar(i, feat, p, costs)
        if fr.a_plus:
            side[i] = int(fr.side)
            a_plus[i] = True
    out = feat[["timestamp"]].copy()
    out["signal"] = side
    out["a_plus"] = a_plus
    return out


# Explicit A+ checklist documentation for research/README
A_PLUS_CHECKLIST = [
    "1. ATR expansion: atr/median_atr >= atr_expand_min (vol breakout)",
    "2. Range break: close clears Donchian(N) by break_buffer_atr * ATR",
    "3. False-break filter: confirm_bars closes hold outside; no deep re-entry",
    "4. Flow proxy: volume surge AND signed-volume z aligned with break",
    "5. Regime: Kaufman ER >= er_min AND EMA fast/slow alignment with side",
    "6. Session gate: UTC hour in allowed London/NY flow window",
    "7. Shock skip: |1-bar return| < shock_ret_abs",
    "8. Fee hurdle: prior expectancy >= min_edge * Kraken RT cost",
]
