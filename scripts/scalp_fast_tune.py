#!/usr/bin/env python3
"""Fast scalp search: many trades, small risk, futures fee, prop DD, target 10-20%/mo."""

from __future__ import annotations

import itertools
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.prop_multi_strategy_search import (  # noqa: E402
    ACCOUNT,
    DAILY_FAIL,
    DD_FAIL,
    load_bars,
    to_arrays,
    build_base,
    ctx_with_sessions,
    gen_signals,
    simulate_trades,
)

# Starter prop
DAILY = 3.0
DD = 6.0
FEE_RT = 0.0004  # futures-like
OUT = ROOT / "data" / "scalp_fast_tune.json"


def equity_path(trades, risk_pct, fee_r_frac):
    """
    Apply trades with prop gates. fee_r_frac = fee as fraction of 1R (fee_rt/sl_pct).
    Returns stats including monthly %.
    """
    equity = ACCOUNT
    peak = ACCOUNT
    max_dd = 0.0
    day = None
    day_start = ACCOUNT
    day_pnl = 0.0
    halted_days = set()
    months = {}
    closed = 0
    wins = 0
    skip = 0

    for exit_ts, entry_ts, pnl_pct, s, dkey in trades:
        # pnl_pct is already risk_pct * R; subtract fee in R units
        # net R = pnl_pct/risk_pct - fee_r_frac
        r_mult = pnl_pct / risk_pct if risk_pct else 0.0
        net_r = r_mult - fee_r_frac
        net_pct = risk_pct * net_r

        if day != dkey:
            day = dkey
            day_start = equity
            day_pnl = 0.0
        if dkey in halted_days:
            skip += 1
            continue

        day_pct = day_pnl / day_start * 100 if day_start else 0.0
        dd = (peak - equity) / peak * 100 if peak else 0.0
        # gate
        if day_pct - risk_pct < -DAILY + 1e-9:
            halted_days.add(dkey)
            skip += 1
            continue
        if dd + risk_pct >= DD - 1e-9:
            skip += 1
            continue

        equity += equity * (net_pct / 100.0)
        day_pnl = equity - day_start
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100 if peak else 0.0)
        closed += 1
        if net_pct > 0:
            wins += 1
        if day_pnl / day_start * 100 <= -DAILY + 1e-9:
            halted_days.add(dkey)

        mk = datetime.fromtimestamp(exit_ts, tz=timezone.utc).strftime("%Y-%m")
        months[mk] = months.get(mk, 0.0) + (equity * 0)  # placeholder
        # store dollar pnl approx via account pct of initial for reporting
        # better: track month pnl in $ from equity delta — redo simply below

    # Re-run for monthly using same logic collecting month pnl
    equity = ACCOUNT
    peak = ACCOUNT
    max_dd = 0.0
    day = None
    day_start = ACCOUNT
    day_pnl = 0.0
    halted_days = set()
    month_pnl = {}
    closed = wins = 0
    worst_day = 0.0

    for exit_ts, entry_ts, pnl_pct, s, dkey in trades:
        r_mult = pnl_pct / risk_pct if risk_pct else 0.0
        net_pct = risk_pct * (r_mult - fee_r_frac)

        if day != dkey:
            if day is not None and day_start:
                worst_day = min(worst_day, day_pnl / day_start * 100)
            day = dkey
            day_start = equity
            day_pnl = 0.0
        if dkey in halted_days:
            continue
        day_pct = day_pnl / day_start * 100 if day_start else 0.0
        dd = (peak - equity) / peak * 100 if peak else 0.0
        if day_pct - risk_pct < -DAILY + 1e-9:
            halted_days.add(dkey)
            continue
        if dd + risk_pct >= DD - 1e-9:
            continue

        before = equity
        equity *= 1.0 + net_pct / 100.0
        day_pnl = equity - day_start
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100 if peak else 0.0)
        closed += 1
        if net_pct > 0:
            wins += 1
        mk = datetime.fromtimestamp(exit_ts, tz=timezone.utc).strftime("%Y-%m")
        month_pnl[mk] = month_pnl.get(mk, 0.0) + (equity - before)
        if day_start and day_pnl / day_start * 100 <= -DAILY + 1e-9:
            halted_days.add(dkey)

    total_pct = (equity - ACCOUNT) / ACCOUNT * 100
    month_rows = [
        {"month": k, "pct": round(v / ACCOUNT * 100, 3), "pnl": round(v, 2)}
        for k, v in sorted(month_pnl.items())
    ]
    # active months with trades
    n_mo = max(1, len(month_rows))
    # use calendar span from first to last trade month ~ 
    per_mo = total_pct / 12.0  # normalize to year of data
    risk_ok = max_dd <= DD + 1e-6 and worst_day >= -(DAILY + 0.05)
    return {
        "trades": closed,
        "wins": wins,
        "win_rate_pct": round(100.0 * wins / closed, 2) if closed else 0.0,
        "total_account_pct": round(total_pct, 3),
        "per_month_simple_pct": round(per_mo, 3),
        "max_dd_pct": round(max_dd, 3),
        "worst_day_pct": round(worst_day, 3),
        "months": month_rows,
        "risk_ok": risk_ok,
        "target_ok": risk_ok and 10.0 <= per_mo <= 22.0 and closed >= 150,
        "final_equity": round(equity, 2),
    }


def main():
    t0 = time.time()
    candles, _ = load_bars(365)
    ts, o, h, l, c = to_arrays(candles)
    base = build_base(ts, o, h, l, c)

    families = ["ema_pullback", "ema_cross", "bb_bounce", "ema_rsi_bb", "rsi_trend", "sr_bounce"]
    sessions = [
        {"LNDN", "NYAM"},
        {"LNDN", "NYAM", "NYPM"},
        {"ASIA", "LNDN", "NYAM", "NYPM"},
        {"LNDN"},
        {"NYAM", "NYPM"},
    ]
    sides_l = ["both", "short", "long"]

    results = []
    best = None
    tested = 0

    signal_cache = []
    for fam, sess, sides in itertools.product(families, sessions, sides_l):
        params = {"sides": sides, "tol": 0.0015, "trend_filter": True, "rsi_os": 30, "rsi_ob": 70}
        ctx = ctx_with_sessions(base, sess)
        sig = gen_signals(fam, params, ctx)
        n = int(np.count_nonzero(sig))
        if n < 80:
            continue
        signal_cache.append((fam, params, sess, sig, ctx, n))

    print(f"Signals cached: {len(signal_cache)}", flush=True)

    risk_grid = [0.2, 0.3, 0.4, 0.5]
    tp_grid = [0.8, 1.0, 1.2, 1.5]
    sl_grid = [0.002, 0.003, 0.004, 0.005]
    cd_grid = [3, 6, 12]  # bars
    mxd_grid = [3, 5, 8]

    for fam, params, sess, sig, ctx, nsig in signal_cache:
        for risk, tp, slp, cd, mxd in itertools.product(risk_grid, tp_grid, sl_grid, cd_grid, mxd_grid):
            if risk >= 0.5 and mxd >= 8:
                continue
            trades = simulate_trades(
                sig, ts, o, h, l, c, ctx["day_id"],
                risk_pct=risk, tp_rr=tp, sl_atr_mult=None, sl_pct=slp,
                cooldown_bars=cd, max_trades_per_day=mxd, use_gates=True,
            )
            tested += 1
            if len(trades) < 80:
                continue
            fee_r = FEE_RT / slp  # fee as R
            fit = equity_path(trades, risk, fee_r)
            if fit["trades"] < 80:
                continue
            row = {
                "family": fam,
                "params": {**params, "sessions": sorted(sess)},
                "risk_pct": risk,
                "tp_rr": tp,
                "sl_pct": slp,
                "cooldown_bars": cd,
                "max_day": mxd,
                "n_signals": nsig,
                "fee_r": round(fee_r, 3),
                **fit,
            }
            results.append(row)
            score = (
                1 if fit["target_ok"] else 0,
                1 if fit["risk_ok"] else 0,
                fit["per_month_simple_pct"] if fit["risk_ok"] else -999,
                fit["win_rate_pct"],
                fit["trades"],
            )
            if best is None or score > best[0]:
                best = (score, row)
                print(
                    f"BEST mo={fit['per_month_simple_pct']:+.2f}% wr={fit['win_rate_pct']:.1f}% "
                    f"dd={fit['max_dd_pct']:.2f}% day={fit['worst_day_pct']:.2f}% n={fit['trades']} "
                    f"{fam} {params['sides']} risk={risk} tp={tp} sl={slp} cd={cd} mxd={mxd} "
                    f"sess={sorted(sess)} ok={fit['risk_ok']}/{fit['target_ok']} [{time.time()-t0:.0f}s]",
                    flush=True,
                )
                for m in fit["months"][:8]:
                    print(f"  {m['month']} {m['pct']:+.2f}%", flush=True)
            if fit["target_ok"]:
                print("*** TARGET 10-20%/mo HIT ***", flush=True)
            if tested % 500 == 0:
                print(f"… tested={tested} kept={len(results)} best_mo={best[1]['per_month_simple_pct'] if best else None} [{time.time()-t0:.0f}s]", flush=True)

    results.sort(
        key=lambda r: (
            1 if r["target_ok"] else 0,
            1 if r["risk_ok"] else 0,
            r["per_month_simple_pct"] if r["risk_ok"] else -999,
            r["win_rate_pct"],
        ),
        reverse=True,
    )
    out = {
        "goal": "10-20%/mo many small trades prop DD fee=0.04%RT",
        "tested": tested,
        "elapsed_sec": round(time.time() - t0, 1),
        "hits": [r for r in results if r["target_ok"]][:20],
        "best": results[0] if results else None,
        "top_risk_ok": [r for r in results if r["risk_ok"]][:20],
    }
    OUT.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nSaved {OUT} hits={len(out['hits'])}", flush=True)
    if out["best"]:
        b = {k: v for k, v in out["best"].items()}
        print(json.dumps(b, indent=2, default=str)[:3000], flush=True)


if __name__ == "__main__":
    main()
