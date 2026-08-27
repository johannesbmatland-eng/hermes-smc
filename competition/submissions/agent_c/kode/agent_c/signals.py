"""A+ setup checklist — ALL filters must pass; default = no trade.

Independent core filters (≥4):
  1) ATR expansion  2) Range break  3) False-break / close-location
  4) Flow proxy     5) Regime ER+EMA  (+ TOD, shock, fee hurdle gates)
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


def expected_R(p: StrategyParams) -> float:
    if p.use_structural_fee_hurdle:
        win_R = p.target_atr_mult * p.structural_win_capture
        return p.prior_hit_rate * win_R - (1.0 - p.prior_hit_rate) * p.prior_avg_loss_R
    return p.prior_hit_rate * p.prior_avg_win_R - (1.0 - p.prior_hit_rate) * p.prior_avg_loss_R


def fee_hurdle_mask(
    atr: np.ndarray,
    price: np.ndarray,
    p: StrategyParams,
    costs: CostModel,
) -> np.ndarray:
    """Require E[$] / round-trip_cost >= min_edge.

    E[$]/unit] = e_R * stop_dist; RT_cost/unit = RT * price
    <=> e_R * (stop_dist/price) >= min_edge * RT
    """
    e_R = expected_R(p)
    if e_R <= 0:
        return np.zeros_like(atr, dtype=bool)
    stop_frac = p.stop_atr_mult * atr / np.maximum(price, 1e-12)
    edge = e_R * stop_frac
    need = p.min_edge_multiple_of_rt_cost * costs.round_trip_frac
    return np.isfinite(atr) & (atr > 0) & (price > 0) & (edge >= need)


def compute_signal_arrays(
    feat: pd.DataFrame,
    params: StrategyParams | None = None,
    costs: CostModel | None = None,
) -> dict[str, np.ndarray]:
    p = params or DEFAULT_PARAMS
    costs = costs or DEFAULT_COSTS
    n = len(feat)

    atr = feat["atr"].to_numpy(dtype=np.float64)
    atr_ratio = feat["atr_ratio"].to_numpy(dtype=np.float64)
    up = feat["donch_up"].to_numpy(dtype=np.float64)
    dn = feat["donch_dn"].to_numpy(dtype=np.float64)
    close = feat["close"].to_numpy(dtype=np.float64)
    open_ = feat["open"].to_numpy(dtype=np.float64)
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

    bar_range = np.maximum(high - low, 1e-12)
    close_loc = (close - low) / bar_range

    # False-break: close held outside + wick did not deeply reclaim + close location
    false_long = (
        long_break
        & (low >= up - p.false_break_reentry_atr * atr)
        & (close_loc >= p.min_close_location)
    )
    false_short = (
        short_break
        & (high <= dn + p.false_break_reentry_atr * atr)
        & (close_loc <= (1.0 - p.min_close_location))
    )
    # multi-bar hold if confirm_bars > 1
    if p.confirm_bars > 1:
        cb = p.confirm_bars
        fl = false_long.copy()
        fs = false_short.copy()
        false_long[:] = False
        false_short[:] = False
        for i in range(cb - 1, n):
            if fl[i] and np.all(close[i - cb + 1 : i + 1] > up[i]):
                false_long[i] = True
            if fs[i] and np.all(close[i - cb + 1 : i + 1] < dn[i]):
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

    long_ok = atr_expand & long_break & false_long & flow_long & regime_long & tod_ok & shock_ok & fee_ok
    short_ok = atr_expand & short_break & false_short & flow_short & regime_short & tod_ok & shock_ok & fee_ok
    both = long_ok & short_ok
    long_ok = long_ok & ~both
    short_ok = short_ok & ~both

    side = np.zeros(n, dtype=np.int8)
    side[long_ok] = 1
    side[short_ok] = -1
    return {
        "side": side,
        "a_plus": side != 0,
        "atr_expand": atr_expand,
        "long_break": long_break,
        "short_break": short_break,
        "fee_ok": fee_ok,
        "false_long": false_long,
        "false_short": false_short,
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
    ok = bool(cache["a_plus"][i])
    side = Side(int(cache["side"][i])) if ok else Side.FLAT
    return FilterResult(
        side=side,
        atr_expand=bool(cache["atr_expand"][i]),
        range_break=bool(cache["long_break"][i] or cache["short_break"][i]),
        false_break_ok=bool(cache["false_long"][i] or cache["false_short"][i]),
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
    "3. False-break filter: wick reclaim limit + close location in break direction",
    "4. Flow proxy: volume surge AND signed-volume z aligned with break",
    "5. Regime: Kaufman ER >= er_min AND EMA fast/slow alignment with side",
    "6. Session gate: UTC London/NY hours; weekends blocked",
    "7. Shock skip: |1-bar return| < shock_ret_abs",
    "8. Fee hurdle: structural expectancy >= min_edge * Kraken RT cost",
]
