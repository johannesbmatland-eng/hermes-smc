#!/usr/bin/env python3
"""
Fast multi-strategy prop-challenge search.

Strategies: EMA trend/pullback/cross, RSI MR/trend, Bollinger bounce/break,
swing S/R bounce/break, simplified SMC FVG touch, and combos.

Fitness = challenge pass rate under Starter rules:
  +10% pass, 3% daily fail, 6% static DD fail, $100k, rebuy on fail/pass.
Target: pass_rate >= 80% with multiple passes.
"""

from __future__ import annotations

import itertools
import json
import math
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.backtest_3m import CACHE_DIR  # noqa: E402

ACCOUNT = 100_000.0
PASS_PCT = 10.0
DAILY_FAIL = 3.0
DD_FAIL = 6.0
NY = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Data / indicators
# ---------------------------------------------------------------------------

def load_bars(prefer_days: int = 365):
    files = sorted(CACHE_DIR.glob(f"okx_BTCUSDT_{prefer_days}d_*_5m.json"), key=lambda p: p.stat().st_size)
    if not files:
        files = sorted(CACHE_DIR.glob("okx_BTCUSDT_*_5m.json"), key=lambda p: p.stat().st_size)
    if not files:
        raise SystemExit("No OHLCV cache")
    f5 = files[-1]
    stem = f5.name.replace("_5m.json", "")
    raw = json.loads(f5.read_text())
    candles = raw["candles"]
    print(f"Loaded {f5.name}: {len(candles)} bars", flush=True)
    return candles, stem


def to_arrays(candles: list[dict]):
    ts = np.array([c["timestamp"] for c in candles], dtype=np.float64)
    o = np.array([c["open"] for c in candles], dtype=np.float64)
    h = np.array([c["high"] for c in candles], dtype=np.float64)
    l = np.array([c["low"] for c in candles], dtype=np.float64)
    c = np.array([c["close"] for c in candles], dtype=np.float64)
    return ts, o, h, l, c


def ema(x: np.ndarray, period: int) -> np.ndarray:
    out = np.empty_like(x)
    out[:] = np.nan
    if len(x) < period:
        return out
    k = 2.0 / (period + 1)
    out[period - 1] = x[:period].mean()
    for i in range(period, len(x)):
        out[i] = x[i] * k + out[i - 1] * (1 - k)
    return out


def rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    out = np.empty_like(close)
    out[:] = np.nan
    if len(close) < period + 1:
        return out
    delta = np.diff(close, prepend=close[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_g = gain[1 : period + 1].mean()
    avg_l = loss[1 : period + 1].mean()
    if avg_l == 0:
        out[period] = 100.0
    else:
        out[period] = 100.0 - (100.0 / (1.0 + avg_g / avg_l))
    for i in range(period + 1, len(close)):
        avg_g = (avg_g * (period - 1) + gain[i]) / period
        avg_l = (avg_l * (period - 1) + loss[i]) / period
        if avg_l == 0:
            out[i] = 100.0
        else:
            out[i] = 100.0 - (100.0 / (1.0 + avg_g / avg_l))
    return out


def bollinger(close: np.ndarray, period: int = 20, mult: float = 2.0):
    mid = np.empty_like(close)
    upper = np.empty_like(close)
    lower = np.empty_like(close)
    mid[:] = upper[:] = lower[:] = np.nan
    for i in range(period - 1, len(close)):
        window = close[i - period + 1 : i + 1]
        m = window.mean()
        s = window.std(ddof=0)
        mid[i] = m
        upper[i] = m + mult * s
        lower[i] = m - mult * s
    return mid, upper, lower


def session_names(ts: np.ndarray) -> np.ndarray:
    """Return session name code per bar: 0 ASIA, 1 LNDN, 2 NYAM, 3 NYPM."""
    out = np.empty(len(ts), dtype=np.int8)
    for i, t in enumerate(ts):
        dt = datetime.fromtimestamp(float(t), tz=NY)
        mins = dt.hour * 60 + dt.minute
        if mins >= 20 * 60 or mins < 2 * 60:
            out[i] = 0
        elif mins < 9 * 60 + 30:
            out[i] = 1
        elif mins < 13 * 60 + 30:
            out[i] = 2
        else:
            out[i] = 3
    return out


def session_mask_from_codes(codes: np.ndarray, sessions: set[str]) -> np.ndarray:
    want = set()
    if "ASIA" in sessions:
        want.add(0)
    if "LNDN" in sessions:
        want.add(1)
    if "NYAM" in sessions:
        want.add(2)
    if "NYPM" in sessions:
        want.add(3)
    return np.isin(codes, list(want))


def day_keys(ts: np.ndarray) -> np.ndarray:
    out = np.empty(len(ts), dtype=np.int32)
    for i, t in enumerate(ts):
        dt = datetime.fromtimestamp(float(t), tz=NY)
        out[i] = dt.year * 10000 + dt.month * 100 + dt.day
    return out


def swing_levels(h, l, left: int = 3, right: int = 3):
    """
    Causal swing high/low: a pivot at center is only known after `right` bars.
    At bar i we expose the last pivot confirmed at or before i.
    """
    n = len(h)
    sh = np.full(n, np.nan)
    sl = np.full(n, np.nan)
    last_hi = np.nan
    last_lo = np.nan
    for i in range(left + right, n):
        center = i - right
        window_h = h[center - left : center + right + 1]
        window_l = l[center - left : center + right + 1]
        if h[center] >= window_h.max():
            last_hi = h[center]
        if l[center] <= window_l.min():
            last_lo = l[center]
        sh[i] = last_hi
        sl[i] = last_lo
    return sh, sl


def detect_fvg_signals(o, h, l, c, lookback: int = 50):
    """
    Simplified FVG: bullish gap c[i-2].high < c[i].low, bearish opposite.
    Signal on bar i when price revisits unmitigated FVG with engulf-ish close.
    Returns side array: 1 long, -1 short, 0 none (on closed bar i).
    """
    n = len(c)
    side = np.zeros(n, dtype=np.int8)
    # store recent FVGs as list of (end_idx, type, top, bot)
    fvgs: list[tuple] = []
    for i in range(2, n):
        # new FVGs formed at i (using i-2,i-1,i) — mark at close of i
        if l[i] > h[i - 2]:  # bullish FVG
            fvgs.append((i, 1, l[i], h[i - 2]))
        if h[i] < l[i - 2]:  # bearish
            fvgs.append((i, -1, l[i - 2], h[i]))
        # prune old
        fvgs = [f for f in fvgs if i - f[0] <= lookback]
        # check revisit on closed bar i (touch + directional close)
        for f in reversed(fvgs):
            end, typ, top, bot = f
            if i <= end + 1:
                continue
            # touch
            if not (l[i] <= top and h[i] >= bot):
                continue
            if typ == 1 and c[i] > o[i] and c[i] > c[i - 1]:
                side[i] = 1
                break
            if typ == -1 and c[i] < o[i] and c[i] < c[i - 1]:
                side[i] = -1
                break
    return side


@dataclass
class StratCfg:
    name: str
    family: str
    params: dict


# ---------------------------------------------------------------------------
# Signal generators (operate on closed bar i → enter at c[i], SL/TP set)
# ---------------------------------------------------------------------------

def gen_signals(family: str, params: dict, ctx: dict) -> np.ndarray:
    """Return int8 side per bar: +1 long, -1 short, 0 flat."""
    c = ctx["c"]
    h = ctx["h"]
    l = ctx["l"]
    o = ctx["o"]
    n = len(c)
    side = np.zeros(n, dtype=np.int8)
    sess = ctx["sess"]
    ema_f = ctx["ema_fast"]
    ema_s = ctx["ema_slow"]
    ema_t = ctx["ema_trend"]
    rsi_v = ctx["rsi"]
    bb_m, bb_u, bb_l = ctx["bb"]
    sh, slv = ctx["swing_hi"], ctx["swing_lo"]
    fvg = ctx["fvg_side"]

    allow_long = params.get("sides", "both") in ("both", "long")
    allow_short = params.get("sides", "both") in ("both", "short")
    start = max(60, params.get("warm", 60))

    if family == "ema_pullback":
        # Trend by slow EMA slope / price vs trend EMA; enter on touch of fast EMA
        for i in range(start, n - 1):
            if not sess[i]:
                continue
            if np.isnan(ema_f[i]) or np.isnan(ema_t[i]):
                continue
            up = c[i] > ema_t[i] and ema_s[i] > ema_t[i]
            dn = c[i] < ema_t[i] and ema_s[i] < ema_t[i]
            # pullback: low wicked to fast EMA then close back above (long)
            if allow_long and up and l[i] <= ema_f[i] <= h[i] and c[i] > ema_f[i] and c[i] > o[i]:
                side[i] = 1
            elif allow_short and dn and l[i] <= ema_f[i] <= h[i] and c[i] < ema_f[i] and c[i] < o[i]:
                side[i] = -1

    elif family == "ema_cross":
        for i in range(start, n - 1):
            if not sess[i] or np.isnan(ema_f[i]) or np.isnan(ema_s[i - 1]):
                continue
            bull = ema_f[i - 1] <= ema_s[i - 1] and ema_f[i] > ema_s[i] and c[i] > ema_t[i]
            bear = ema_f[i - 1] >= ema_s[i - 1] and ema_f[i] < ema_s[i] and c[i] < ema_t[i]
            if allow_long and bull:
                side[i] = 1
            elif allow_short and bear:
                side[i] = -1

    elif family == "rsi_mr":
        lo = params.get("rsi_os", 30)
        hi = params.get("rsi_ob", 70)
        for i in range(start, n - 1):
            if not sess[i] or np.isnan(rsi_v[i]) or np.isnan(rsi_v[i - 1]):
                continue
            if allow_long and rsi_v[i - 1] < lo <= rsi_v[i] and c[i] > o[i]:
                side[i] = 1
            elif allow_short and rsi_v[i - 1] > hi >= rsi_v[i] and c[i] < o[i]:
                side[i] = -1

    elif family == "rsi_trend":
        # Only with-trend RSI resets
        for i in range(start, n - 1):
            if not sess[i] or np.isnan(rsi_v[i]) or np.isnan(ema_t[i]):
                continue
            up = c[i] > ema_t[i]
            dn = c[i] < ema_t[i]
            if allow_long and up and 40 <= rsi_v[i] <= 55 and c[i] > o[i] and rsi_v[i] > rsi_v[i - 1]:
                side[i] = 1
            elif allow_short and dn and 45 <= rsi_v[i] <= 60 and c[i] < o[i] and rsi_v[i] < rsi_v[i - 1]:
                side[i] = -1

    elif family == "bb_bounce":
        for i in range(start, n - 1):
            if not sess[i] or np.isnan(bb_l[i]):
                continue
            if allow_long and l[i] <= bb_l[i] and c[i] > bb_l[i] and c[i] > o[i]:
                if not params.get("trend_filter") or c[i] > ema_t[i]:
                    side[i] = 1
            elif allow_short and h[i] >= bb_u[i] and c[i] < bb_u[i] and c[i] < o[i]:
                if not params.get("trend_filter") or c[i] < ema_t[i]:
                    side[i] = -1

    elif family == "bb_break":
        for i in range(start, n - 1):
            if not sess[i] or np.isnan(bb_u[i]):
                continue
            if allow_long and c[i] > bb_u[i] and c[i - 1] <= bb_u[i - 1] and c[i] > ema_t[i]:
                side[i] = 1
            elif allow_short and c[i] < bb_l[i] and c[i - 1] >= bb_l[i - 1] and c[i] < ema_t[i]:
                side[i] = -1

    elif family == "sr_bounce":
        tol = params.get("tol", 0.0015)
        for i in range(start, n - 1):
            if not sess[i] or np.isnan(slv[i]) or np.isnan(sh[i]):
                continue
            # Require rejection: long wick beyond level then close back inside
            if allow_long and l[i] <= slv[i] * (1 + tol) and c[i] > slv[i] and c[i] > o[i]:
                if (c[i] - l[i]) / max(1e-9, h[i] - l[i]) >= 0.55:
                    side[i] = 1
            elif allow_short and h[i] >= sh[i] * (1 - tol) and c[i] < sh[i] and c[i] < o[i]:
                if (h[i] - c[i]) / max(1e-9, h[i] - l[i]) >= 0.55:
                    side[i] = -1

    elif family == "sr_break":
        for i in range(start, n - 1):
            if not sess[i] or np.isnan(sh[i - 1]) or np.isnan(slv[i - 1]):
                continue
            if allow_long and c[i] > sh[i - 1] and c[i - 1] <= sh[i - 1] and c[i] > ema_t[i]:
                side[i] = 1
            elif allow_short and c[i] < slv[i - 1] and c[i - 1] >= slv[i - 1] and c[i] < ema_t[i]:
                side[i] = -1

    elif family == "smc_fvg":
        for i in range(start, n - 1):
            if not sess[i]:
                continue
            s = int(fvg[i])
            if s == 1 and allow_long:
                if not params.get("trend_filter") or c[i] > ema_t[i]:
                    side[i] = 1
            elif s == -1 and allow_short:
                if not params.get("trend_filter") or c[i] < ema_t[i]:
                    side[i] = -1

    elif family == "ema_rsi_bb":
        # Confluence: trend + RSI mid + BB side
        for i in range(start, n - 1):
            if not sess[i] or np.isnan(rsi_v[i]) or np.isnan(bb_l[i]) or np.isnan(ema_t[i]):
                continue
            up = c[i] > ema_t[i] and ema_f[i] > ema_s[i]
            dn = c[i] < ema_t[i] and ema_f[i] < ema_s[i]
            if allow_long and up and rsi_v[i] < 55 and l[i] <= bb_m[i] and c[i] > o[i]:
                side[i] = 1
            elif allow_short and dn and rsi_v[i] > 45 and h[i] >= bb_m[i] and c[i] < o[i]:
                side[i] = -1

    else:
        raise ValueError(family)

    return side


# ---------------------------------------------------------------------------
# Trade sim + prop challenge fitness
# ---------------------------------------------------------------------------

def simulate_trades(
    side: np.ndarray,
    ts, o, h, l, c,
    day_id: np.ndarray,
    risk_pct: float,
    tp_rr: float,
    sl_atr_mult: float | None,
    sl_pct: float,
    cooldown_bars: int,
    max_trades_per_day: int,
    use_gates: bool,
):
    """
    Enter at close of signal bar. SL = sl_pct below/above entry (or ATR-like range).
    TP at tp_rr * risk. Exit on bar OHLC path (SL before TP if both).
    Returns list of (exit_ts, pnl_pct_of_equity_at_entry) using fixed fractional risk.
    """
    n = len(c)
    trades = []
    i = 60
    last_exit = -10_000
    cur_day = -1
    day_count = 0

    # Precompute simple range proxy for SL
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))))
    tr[0] = h[0] - l[0]
    atr = ema(tr, 14)

    while i < n - 2:
        if side[i] == 0:
            i += 1
            continue
        if i - last_exit < cooldown_bars:
            i += 1
            continue
        if day_id[i] != cur_day:
            cur_day = day_id[i]
            day_count = 0
        if day_count >= max_trades_per_day:
            i += 1
            continue

        s = int(side[i])
        entry = c[i]
        if sl_atr_mult and not np.isnan(atr[i]) and atr[i] > 0:
            risk_dist = atr[i] * sl_atr_mult
        else:
            risk_dist = entry * sl_pct
        if risk_dist <= 0:
            i += 1
            continue
        if s > 0:
            sl = entry - risk_dist
            tp = entry + risk_dist * tp_rr
        else:
            sl = entry + risk_dist
            tp = entry - risk_dist * tp_rr

        # walk forward for exit
        exited = False
        pnl_r = 0.0
        exit_i = i + 1
        for j in range(i + 1, min(n, i + 1 + 72)):  # max ~6h hold on 5m
            # path: assume adverse first on same bar if both touched
            if s > 0:
                hit_sl = l[j] <= sl
                hit_tp = h[j] >= tp
                if hit_sl and hit_tp:
                    pnl_r = -1.0
                    exit_i = j
                    exited = True
                    break
                if hit_sl:
                    pnl_r = -1.0
                    exit_i = j
                    exited = True
                    break
                if hit_tp:
                    pnl_r = tp_rr
                    exit_i = j
                    exited = True
                    break
            else:
                hit_sl = h[j] >= sl
                hit_tp = l[j] <= tp
                if hit_sl and hit_tp:
                    pnl_r = -1.0
                    exit_i = j
                    exited = True
                    break
                if hit_sl:
                    pnl_r = -1.0
                    exit_i = j
                    exited = True
                    break
                if hit_tp:
                    pnl_r = tp_rr
                    exit_i = j
                    exited = True
                    break
        if not exited:
            # time exit at last bar close
            exit_i = min(n - 1, i + 72)
            move = (c[exit_i] - entry) / risk_dist if s > 0 else (entry - c[exit_i]) / risk_dist
            pnl_r = float(move)

        # pnl as % of equity = risk_pct * R
        pnl_pct = risk_pct * pnl_r
        trades.append((float(ts[exit_i]), float(ts[i]), pnl_pct, s, day_id[exit_i]))
        last_exit = exit_i
        day_count += 1
        i = exit_i + 1

    return trades


def prop_fitness(trades, risk_pct: float, use_gates: bool = True):
    """
    Sequential challenges with rebuy on pass/fail.
    Gates: skip trade if it could breach daily/DD (approximate using known risk).
    Returns dict with pass_rate, passes, fails, etc.
    """
    if len(trades) < 15:
        return {
            "passes": 0,
            "fails": 0,
            "pass_rate": 0.0,
            "attempts": 0,
            "ok": False,
            "reason": "too_few_trades",
        }

    equity = ACCOUNT
    ch_start = ACCOUNT
    day = None
    day_start = ACCOUNT
    day_pnl_pct_equity = 0.0  # tracked in $ then convert

    passes = 0
    fails = 0
    pass_dates = []
    fail_dates = []
    skipped = 0

    # work in dollars; each trade pnl_$ = equity * (pnl_pct/100) but pnl_pct already is % of equity at entry
    for exit_ts, entry_ts, pnl_pct, s, dkey in trades:
        # new day
        if day != dkey:
            day = dkey
            day_start = equity
            day_pnl = 0.0
        else:
            day_pnl = equity - day_start  # will recompute after

        # protective gate before applying (we know this trade's risk)
        day_pnl = equity - day_start
        day_pct = day_pnl / day_start * 100 if day_start else 0.0
        dd_pct = (ch_start - equity) / ch_start * 100 if ch_start else 0.0

        if use_gates:
            if day_pct - risk_pct <= -DAILY_FAIL + 1e-9:
                skipped += 1
                continue
            after = equity * (1 - risk_pct / 100)
            dd_after = (ch_start - after) / ch_start * 100
            if dd_after >= DD_FAIL - 1e-9:
                skipped += 1
                continue

        # apply trade
        pnl_usd = equity * (pnl_pct / 100.0)
        equity += pnl_usd
        day_pnl = equity - day_start
        day_pct = day_pnl / day_start * 100 if day_start else 0.0
        dd_pct = (ch_start - equity) / ch_start * 100 if ch_start else 0.0
        fr = (equity - ch_start) / ch_start * 100 if ch_start else 0.0
        when = datetime.fromtimestamp(exit_ts, tz=timezone.utc).strftime("%Y-%m-%d")

        if day_pct <= -DAILY_FAIL + 1e-9 or dd_pct >= DD_FAIL - 1e-9:
            fails += 1
            fail_dates.append(when)
            equity = ACCOUNT
            ch_start = ACCOUNT
            day_start = ACCOUNT
            continue

        if fr >= PASS_PCT - 1e-9:
            passes += 1
            pass_dates.append(when)
            equity = ACCOUNT
            ch_start = ACCOUNT
            day_start = ACCOUNT

    attempts = passes + fails
    # if ended mid-challenge without fail, don't count as fail
    pass_rate = (passes / attempts) if attempts else 0.0
    return {
        "passes": passes,
        "fails": fails,
        "attempts": attempts,
        "pass_rate": round(pass_rate, 4),
        "pass_dates": pass_dates,
        "fail_dates": fail_dates,
        "skipped": skipped,
        "ok": pass_rate >= 0.80 and passes >= 5 and attempts >= 8,
        "final_equity": round(equity, 2),
    }


def build_base(ts, o, h, l, c):
    print("  base indicators + session/day maps…", flush=True)
    t0 = time.time()
    codes = session_names(ts)
    dkeys = day_keys(ts)
    print(f"  maps done [{time.time()-t0:.1f}s]", flush=True)
    return {
        "ts": ts,
        "o": o,
        "h": h,
        "l": l,
        "c": c,
        "sess_codes": codes,
        "day_id": dkeys,
        "ema_fast": ema(c, 9),
        "ema_slow": ema(c, 21),
        "ema_trend": ema(c, 50),
        "ema_trend100": ema(c, 100),
        "rsi": rsi(c, 14),
        "bb": bollinger(c, 20, 2.0),
        "swing_hi": swing_levels(h, l)[0],
        "swing_lo": swing_levels(h, l)[1],
        "fvg_side": detect_fvg_signals(o, h, l, c),
    }


def ctx_with_sessions(base: dict, sessions: set[str], trend100: bool = False):
    ctx = dict(base)
    ctx["sess"] = session_mask_from_codes(base["sess_codes"], sessions)
    if trend100:
        ctx["ema_trend"] = base["ema_trend100"]
    return ctx


def search():
    candles, stem = load_bars(365)
    ts, o, h, l, c = to_arrays(candles)
    t0 = time.time()

    session_sets = [
        {"ASIA", "LNDN"},
        {"LNDN", "NYAM"},
        {"ASIA", "LNDN", "NYPM"},
        {"LNDN"},
        {"ASIA", "LNDN", "NYAM", "NYPM"},
    ]

    families = [
        "ema_pullback",
        "ema_cross",
        "rsi_mr",
        "rsi_trend",
        "bb_bounce",
        "bb_break",
        "sr_bounce",
        "sr_break",
        "smc_fvg",
        "ema_rsi_bb",
    ]

    # Tight grids first — refine winners later
    risk_grid = [0.5, 0.75, 1.0]
    tp_grid = [1.0, 1.2, 1.5]
    sl_pct_grid = [0.0025, 0.004]
    sides_grid = ["both", "short", "long"]
    cooldown_grid = [12, 24]  # bars of 5m
    max_day_grid = [1, 2]

    results = []
    tested = 0
    best = None

    base = build_base(ts, o, h, l, c)
    contexts = {}
    for sess in session_sets:
        for trend100 in (False, True):
            key = ("|".join(sorted(sess)), trend100)
            contexts[key] = ctx_with_sessions(base, sess, trend100=trend100)

    cfgs = []
    for fam, sess, sides, rsi_os, rsi_ob, trend_f, tol in itertools.product(
        families,
        session_sets,
        sides_grid,
        [25, 30],
        [70, 75],
        [False, True],
        [0.001, 0.0015],
    ):
        # prune nonsensical combos lightly
        if fam in ("rsi_mr",) and sides == "long" and False:
            pass
        params = {
            "sides": sides,
            "rsi_os": rsi_os,
            "rsi_ob": rsi_ob,
            "trend_filter": trend_f if fam in ("bb_bounce", "smc_fvg") else False,
            "tol": tol,
            "sessions": sess,
        }
        cfgs.append((fam, params, sess))

    # Dedup by fam+sides+sess+key params
    seen = set()
    uniq = []
    for fam, params, sess in cfgs:
        k = (fam, params["sides"], tuple(sorted(sess)), params.get("rsi_os"), params.get("trend_filter"), params.get("tol"))
        if k in seen:
            continue
        seen.add(k)
        uniq.append((fam, params, sess))

    print(f"Signal configs: {len(uniq)} × risk/tp/sl grids", flush=True)

    # Prioritize families likely for prop
    def prio(item):
        fam, params, sess = item
        s = 0
        if fam in ("ema_pullback", "ema_rsi_bb", "bb_bounce", "rsi_trend", "smc_fvg"):
            s += 5
        if params["sides"] == "both":
            s += 2
        if sess == {"ASIA", "LNDN"} or sess == {"LNDN", "NYAM"}:
            s += 2
        return -s

    uniq.sort(key=prio)

    # Cap signal configs then full risk grid
    SIGNAL_LIMIT = 48
    uniq = uniq[:SIGNAL_LIMIT]

    for fam, params, sess in uniq:
        ctx = contexts[("|".join(sorted(sess)), False)]
        sig = gen_signals(fam, params, ctx)
        nsig = int(np.count_nonzero(sig))
        if nsig < 20:
            continue

        for risk, tp, slp, cd, mxd in itertools.product(
            risk_grid, tp_grid, sl_pct_grid, cooldown_grid, max_day_grid
        ):
            # prune: high risk + low tp bad for prop
            if risk >= 1.25 and tp < 1.0:
                continue
            if risk >= 1.0 and mxd >= 3 and cd <= 6:
                continue

            trades = simulate_trades(
                sig, ts, o, h, l, c, ctx["day_id"],
                risk_pct=risk,
                tp_rr=tp,
                sl_atr_mult=None,
                sl_pct=slp,
                cooldown_bars=cd,
                max_trades_per_day=mxd,
                use_gates=True,
            )
            fit = prop_fitness(trades, risk_pct=risk, use_gates=True)
            tested += 1
            row = {
                "family": fam,
                "params": {**params, "sessions": sorted(sess)},
                "risk_pct": risk,
                "tp_rr": tp,
                "sl_pct": slp,
                "cooldown_bars": cd,
                "max_day": mxd,
                "n_signals": nsig,
                "n_trades": len(trades),
                **{k: fit[k] for k in ("passes", "fails", "attempts", "pass_rate", "ok", "skipped")},
                "pass_dates": fit.get("pass_dates"),
                "fail_dates": fit.get("fail_dates"),
            }
            if fit["attempts"] >= 5:
                results.append(row)
                if best is None or (
                    (fit["pass_rate"], fit["passes"], -fit["fails"])
                    > (best["pass_rate"], best["passes"], -best["fails"])
                ):
                    best = row
                    print(
                        f"NEW BEST pr={fit['pass_rate']:.1%} P={fit['passes']} F={fit['fails']} "
                        f"{fam} sides={params['sides']} risk={risk} tp={tp} "
                        f"sess={sorted(sess)} trades={len(trades)} [{time.time()-t0:.0f}s]",
                        flush=True,
                    )
                if fit["ok"]:
                    print("*** TARGET HIT ≥80% ***", flush=True)

            if tested % 200 == 0:
                pr = best["pass_rate"] if best else 0
                print(f"… tested={tested} pool={len(results)} best_pr={pr:.1%} [{time.time()-t0:.0f}s]", flush=True)

    results.sort(key=lambda r: (r["pass_rate"], r["passes"], -r["fails"], r["n_trades"]), reverse=True)
    top = results[:30]
    out = {
        "goal": "prop_pass_rate>=80%",
        "rules": {"pass": PASS_PCT, "daily": DAILY_FAIL, "dd": DD_FAIL},
        "tested": tested,
        "elapsed_sec": round(time.time() - t0, 1),
        "best": best,
        "top": top,
        "hits_80": [r for r in results if r.get("ok")],
    }
    path = ROOT / "data" / "prop_multi_strategy_search.json"
    path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nSaved {path} tested={tested} hits={len(out['hits_80'])}", flush=True)
    if best:
        print("BEST:", json.dumps(best, indent=2, default=str)[:2000], flush=True)
    return out


if __name__ == "__main__":
    search()
