"""A+ setup checklist — ALL filters must pass; default = no trade.

Independent core filters (≥4):
  1) ATR expansion  2) Range break  3) False-break hold
  4) Flow proxy     5) Regime/ER+EMA  (+ TOD, shock, fee hurdle gates)
"""

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


def fee_hurdle_mask(
    atr: np.ndarray,
    price: np.ndarray,
    p: StrategyParams,
    costs: CostModel,
) -> np.ndarray:
    stop = p.stop_atr_mult * atr
    e_R = p.prior_hit_rate * p.prior_avg_win_R - (1.0 - p.prior_hit_rate) * p.prior_avg_loss_R
    if e_R <= 0:
        return np.zeros_like(atr, dtype=bool)
    e_price = e_R * stop
    rt = costs.round_trip_frac * price
    ok = (e_price >= p.min_edge_multiple_of_rt_cost * rt) & np.isfinite(atr) & (atr > 0) & (price > 0)
    return ok


def compute_signal_arrays(
    feat: pd.DataFrame,
    params: StrategyParams | None = None,
    costs: CostModel | None = None,
) -> dict[str, np.ndarray]:
    """Vectorized A+ mask + side array (side at i → enter next open)."""
    p = params or DEFAULT_PARAMS
    costs = costs or DEFAULT_COSTS
    n = len(feat)

    atr = feat["atr"].to_numpy(dtype=np.float64)
    atr_ratio = feat["atr_ratio"].to_numpy(dtype=np.float64)
    up = feat["donch_up"].to_numpy(dtype=np.float64)
    dn = feat["donch_dn"].to_numpy(dtype=np.float64)
    close = feat["close"].to_numpy(dtype=np.float64)
    low = feat["low"].to_numpy(dtype=np.float64)
    high = feat["high"].to_numpy(dtype=np.float64)
    er = feat["er"].to_numpy(dtype=np.float64)
    flow_z = feat["flow_z"].to_numpy(dtype=np.float64)
    vol_ratio = feat["vol_ratio"].to_numpy(dtype=np.float64)
    ema_f = feat["ema_fast"].to_numpy(dtype=np.float64)
    ema_s = feat["ema_slow"].to_numpy(dtype=np.float64)
    hour = feat["hour"].to_numpy(dtype=np.int16)
    dow = feat["dow"].to_numpy(dtype=np.int16)
    ret1 = feat["ret1"].to_numpy(dtype=np.float64)

    buf = p.break_buffer_atr * atr
    atr_expand = np.isfinite(atr_ratio) & (atr_ratio >= p.atr_expand_min)
    long_break = np.isfinite(up) & (close > up + buf)
    short_break = np.isfinite(dn) & (close < dn - buf)

    # False-break: last confirm_bars closes outside; no deep re-entry
    false_long = np.zeros(n, dtype=bool)
    false_short = np.zeros(n, dtype=bool)
    cb = p.confirm_bars
    for i in range(cb - 1, n):
        if long_break[i]:
            cl = close[i - cb + 1 : i + 1]
            lo = low[i - cb + 1 : i + 1]
            if np.all(cl > up[i]) and not np.any(lo < up[i] - p.false_break_reentry_atr * atr[i]):
                false_long[i] = True
        if short_break[i]:
            cl = close[i - cb + 1 : i + 1]
            hi = high[i - cb + 1 : i + 1]
            if np.all(cl < dn[i]) and not np.any(hi > dn[i] + p.false_break_reentry_atr * atr[i]):
                false_short[i] = True

    flow_long = (vol_ratio >= p.vol_surge_min) & np.isfinite(flow_z) & (flow_z >= p.flow_z_min)
    flow_short = (vol_ratio >= p.vol_surge_min) & np.isfinite(flow_z) & (flow_z <= -p.flow_z_min)

    regime_base = np.isfinite(er) & (er >= p.er_min)
    if p.require_ema_align:
        regime_long = regime_base & np.isfinite(ema_f) & np.isfinite(ema_s) & (ema_f > ema_s) & (close > ema_f)
        regime_short = regime_base & np.isfinite(ema_f) & np.isfinite(ema_s) & (ema_f < ema_s) & (close < ema_f)
    else:
        regime_long = regime_base
        regime_short = regime_base

    allowed = np.isin(hour, np.array(p.allowed_hours_utc, dtype=np.int16))
    blocked = np.isin(dow, np.array(p.blocked_dow, dtype=np.int16)) if p.blocked_dow else np.zeros(n, dtype=bool)
    tod_ok = allowed & ~blocked
    shock_ok = np.isfinite(ret1) & (np.abs(ret1) < p.shock_ret_abs)
    fee_ok = fee_hurdle_mask(atr, close, p, costs)

    long_ok = (
        atr_expand & long_break & false_long & flow_long & regime_long & tod_ok & shock_ok & fee_ok
    )
    short_ok = (
        atr_expand & short_break & false_short & flow_short & regime_short & tod_ok & shock_ok & fee_ok
    )
    # if both (rare), skip
    both = long_ok & short_ok
    long_ok = long_ok & ~both
    short_ok = short_ok & ~both

    side = np.zeros(n, dtype=np.int8)
    side[long_ok] = 1
    side[short_ok] = -1
    a_plus = side != 0
    return {
        "side": side,
        "a_plus": a_plus,
        "atr_expand": atr_expand,
        "long_break": long_break,
        "short_break": short_break,
        "fee_ok": fee_ok,
    }


def evaluate_bar(
    i: int,
    feat: pd.DataFrame,
    params: StrategyParams | None = None,
    costs: CostModel | None = None,
    cache: dict[str, np.ndarray] | None = None,
) -> FilterResult:
    p = params or DEFAULT_PARAMS
    costs = costs or DEFAULT_COSTS
    if cache is None:
        cache = compute_signal_arrays(feat, p, costs)
    side = Side(int(cache["side"][i]))
    ok = bool(cache["a_plus"][i])
    return FilterResult(
        side=side if ok else Side.FLAT,
        atr_expand=bool(cache["atr_expand"][i]),
        range_break=bool(cache["long_break"][i] or cache["short_break"][i]),
        false_break_ok=ok,
        flow_ok=ok,
        regime_ok=ok,
        tod_ok=ok,
        shock_ok=ok,
        fee_hurdle_ok=bool(cache["fee_ok"][i]),
        reasons=() if ok else ("filtered",),
    )


def generate_signals(
    feat: pd.DataFrame,
    params: StrategyParams | None = None,
    costs: CostModel | None = None,
) -> pd.DataFrame:
    cache = compute_signal_arrays(feat, params, costs)
    out = feat[["timestamp"]].copy()
    out["signal"] = cache["side"]
    out["a_plus"] = cache["a_plus"]
    return out


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
