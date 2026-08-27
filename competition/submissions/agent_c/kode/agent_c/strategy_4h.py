"""4H macro-flow A+ breakout engine.

A+ checklist (ALL must pass):
  1. ATR expansion vs median baseline (vol breakout regime)
  2. Donchian range break in EMA-trend direction
  3. False-break filter (close location + wick reclaim limit)
     OR pullback-continuation after structure held
  4. Volume surge flow proxy at break
  5. Regime: Kaufman ER + EMA separation
  6. Weekday session gate
  7. Fee hurdle: structural E[R]*stop_frac >= RT cost
Default = no trade.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import CostModel, StrategyParams, DEFAULT_COSTS, DEFAULT_PARAMS
from .features import true_range, wilder_atr


@dataclass
class SignalEvent:
    bar_idx: int
    side: int
    lookback: int
    mode: str  # "direct" | "pullback"
    timestamp: pd.Timestamp
    atr: float


A_PLUS_CHECKLIST = [
    "1. ATR expansion: atr/median_atr >= atr_expand_min (vol breakout)",
    "2. Range break: close clears Donchian(lookback) with EMA trend align",
    "3. False-break filter: close-location + wick reclaim OR pullback after structure hold",
    "4. Flow proxy: volume/SMA >= vol_surge_min at break",
    "5. Regime: Kaufman ER >= er_min AND EMA fast/slow separation",
    "6. Session gate: weekdays only (weekend thin flow blocked)",
    "7. Fee hurdle: prior E[R]*stop_frac >= min_edge * Kraken RT cost",
]


def _er(close: np.ndarray, length: int) -> np.ndarray:
    out = np.full_like(close, np.nan, dtype=np.float64)
    for i in range(length, len(close)):
        net = abs(close[i] - close[i - length])
        path = np.sum(np.abs(np.diff(close[i - length : i + 1])))
        out[i] = net / path if path > 1e-12 else 0.0
    return out


def _donchian(high: np.ndarray, low: np.ndarray, length: int) -> tuple[np.ndarray, np.ndarray]:
    up = np.full_like(high, np.nan, dtype=np.float64)
    dn = np.full_like(low, np.nan, dtype=np.float64)
    for i in range(length, len(high)):
        up[i] = np.max(high[i - length : i])
        dn[i] = np.min(low[i - length : i])
    return up, dn


def expected_R(p: StrategyParams) -> float:
    return p.prior_hit_rate * p.prior_avg_win_R - (1.0 - p.prior_hit_rate) * p.prior_avg_loss_R


def fee_ok(atr: float, price: float, p: StrategyParams, costs: CostModel) -> bool:
    e_R = expected_R(p)
    if e_R <= 0 or atr <= 0 or price <= 0:
        return False
    stop_frac = p.stop_atr_mult * atr / price
    return e_R * stop_frac >= p.min_edge_multiple_of_rt_cost * costs.round_trip_frac


def generate_a_plus_events(
    df: pd.DataFrame,
    params: StrategyParams | None = None,
    costs: CostModel | None = None,
) -> list[SignalEvent]:
    """Scan 4H (or any bar) frame for A+ events across lookbacks."""
    p = params or DEFAULT_PARAMS
    costs = costs or DEFAULT_COSTS
    h = df["high"].to_numpy(dtype=np.float64)
    l = df["low"].to_numpy(dtype=np.float64)
    c = df["close"].to_numpy(dtype=np.float64)
    o = df["open"].to_numpy(dtype=np.float64)
    v = df["volume"].to_numpy(dtype=np.float64)
    ts = pd.to_datetime(df["timestamp"])
    n = len(df)

    atr = wilder_atr(true_range(h, l, c), p.atr_len)
    atr_med = pd.Series(atr).rolling(p.atr_baseline_len).median().to_numpy()
    atr_r = atr / atr_med
    ema_f = pd.Series(c).ewm(span=p.ema_fast, adjust=False).mean().to_numpy()
    ema_s = pd.Series(c).ewm(span=p.ema_slow, adjust=False).mean().to_numpy()
    vol_sma = pd.Series(v).rolling(p.vol_sma_len).mean().to_numpy()
    vol_r = v / np.maximum(vol_sma, 1e-12)
    dow = ts.dt.dayofweek.to_numpy()

    events: list[SignalEvent] = []
    for lb in p.lookbacks:
        up, dn = _donchian(h, l, lb)
        er = _er(c, lb)
        i = max(lb, p.atr_baseline_len, p.ema_slow) + 5
        while i < n - p.time_stop_bars - 2:
            if dow[i] in p.blocked_dow:
                i += 1
                continue
            if not (np.isfinite(atr_r[i]) and atr_r[i] >= p.atr_expand_min):
                i += 1
                continue
            if not (np.isfinite(vol_r[i]) and vol_r[i] >= p.vol_surge_min):
                i += 1
                continue

            side = 0
            if np.isfinite(up[i]) and c[i] > up[i] + p.break_buffer_atr * atr[i]:
                if (not p.require_ema_align) or (ema_f[i] > ema_s[i]):
                    side = 1
            elif np.isfinite(dn[i]) and c[i] < dn[i] - p.break_buffer_atr * atr[i]:
                if (not p.require_ema_align) or (ema_f[i] < ema_s[i]):
                    side = -1
            if side == 0:
                i += 1
                continue

            # Regime at break
            if not (np.isfinite(er[i]) and er[i] >= p.er_min):
                i += 1
                continue
            if abs(ema_f[i] - ema_s[i]) / c[i] < p.ema_sep_min:
                i += 1
                continue

            br = max(h[i] - l[i], 1e-12)
            cloc = (c[i] - l[i]) / br
            false_ok = (
                (side > 0 and cloc >= p.min_close_location and l[i] >= up[i] - p.false_break_reentry_atr * atr[i])
                or (side < 0 and cloc <= 1.0 - p.min_close_location and h[i] <= dn[i] + p.false_break_reentry_atr * atr[i])
            )

            entry_j = None
            mode = ""
            if p.allow_direct_entry and false_ok and fee_ok(float(atr[i]), float(c[i]), p, costs):
                entry_j = i
                mode = "direct"

            if entry_j is None and p.allow_pullback_entry:
                for k in range(1, p.pullback_window + 1):
                    j = i + k
                    if j >= n - 2:
                        break
                    if dow[j] in p.blocked_dow:
                        break
                    ok = (
                        (side > 0 and l[j] <= ema_f[j] and c[j] > ema_f[j] and c[j] > o[j])
                        or (side < 0 and h[j] >= ema_f[j] and c[j] < ema_f[j] and c[j] < o[j])
                    )
                    if not ok:
                        continue
                    if not (np.isfinite(er[j]) and er[j] >= p.er_min):
                        break
                    if abs(ema_f[j] - ema_s[j]) / c[j] < p.ema_sep_min:
                        break
                    # structure hold (false-break survivor)
                    if side > 0 and np.min(l[i : j + 1]) < up[i] - p.structure_hold_atr * atr[i]:
                        break
                    if side < 0 and np.max(h[i : j + 1]) > dn[i] + p.structure_hold_atr * atr[i]:
                        break
                    if not fee_ok(float(atr[j]), float(c[j]), p, costs):
                        break
                    entry_j = j
                    mode = "pullback"
                    break

            if entry_j is None:
                i += 1
                continue

            events.append(
                SignalEvent(
                    bar_idx=entry_j,
                    side=side,
                    lookback=lb,
                    mode=mode,
                    timestamp=pd.Timestamp(ts.iloc[entry_j]),
                    atr=float(atr[entry_j]),
                )
            )
            i = entry_j + max(p.cooldown_bars, 2)

    # Dedup by time
    events.sort(key=lambda e: e.timestamp)
    deduped: list[SignalEvent] = []
    last_t = None
    for e in events:
        if last_t is not None and (e.timestamp - last_t).total_seconds() < p.dedup_hours * 3600:
            continue
        deduped.append(e)
        last_t = e.timestamp
    return deduped


def false_break_stats(df: pd.DataFrame, lookback: int = 20) -> dict:
    """Raw break vs false-break (reclaim within 3 bars) rates for research."""
    h = df["high"].to_numpy(dtype=np.float64)
    l = df["low"].to_numpy(dtype=np.float64)
    c = df["close"].to_numpy(dtype=np.float64)
    up, dn = _donchian(h, l, lookback)
    breaks = 0
    false_b = 0
    for i in range(lookback, len(c) - 4):
        if not np.isfinite(up[i]):
            continue
        if c[i] > up[i]:
            breaks += 1
            if np.any(c[i + 1 : i + 4] < up[i]):
                false_b += 1
        elif c[i] < dn[i]:
            breaks += 1
            if np.any(c[i + 1 : i + 4] > dn[i]):
                false_b += 1
    return {
        "lookback": lookback,
        "raw_breaks": breaks,
        "false_within_3bars": false_b,
        "false_rate": (false_b / breaks) if breaks else 0.0,
        "hold_rate": 1.0 - ((false_b / breaks) if breaks else 0.0),
    }
