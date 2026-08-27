#!/usr/bin/env python3
"""
Prop multi-strategy search v2 — soft-fail when stuck near DD, refine best families.
Target: ≥80% challenge pass rate with multiple passes (Starter: +10% / 3% day / 6% DD).
"""

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
    PASS_PCT,
    DAILY_FAIL,
    DD_FAIL,
    load_bars,
    to_arrays,
    build_base,
    ctx_with_sessions,
    gen_signals,
    simulate_trades,
)

OUT = ROOT / "data" / "prop_multi_strategy_search_v2.json"


def prop_fitness_v2(trades, risk_pct: float, use_gates: bool = True, stuck_skip_limit: int = 40):
    """
    Rebuy on pass/fail. If protective gates skip `stuck_skip_limit` trades in a row
    while drawdown > 2%, treat as locked challenge FAIL and rebuy.
    """
    if len(trades) < 20:
        return {
            "passes": 0,
            "fails": 0,
            "attempts": 0,
            "pass_rate": 0.0,
            "ok": False,
            "reason": "too_few_trades",
            "skipped": 0,
        }

    equity = ACCOUNT
    ch_start = ACCOUNT
    day = None
    day_start = ACCOUNT
    passes = fails = skipped = 0
    skip_streak = 0
    pass_dates, fail_dates = [], []

    for exit_ts, entry_ts, pnl_pct, s, dkey in trades:
        if day != dkey:
            day = dkey
            day_start = equity

        day_pnl = equity - day_start
        day_pct = day_pnl / day_start * 100 if day_start else 0.0
        dd_pct = (ch_start - equity) / ch_start * 100 if ch_start else 0.0

        if use_gates:
            block = False
            if day_pct - risk_pct <= -DAILY_FAIL + 1e-9:
                block = True
            else:
                after = equity * (1 - risk_pct / 100)
                if (ch_start - after) / ch_start * 100 >= DD_FAIL - 1e-9:
                    block = True
            if block:
                skipped += 1
                skip_streak += 1
                if skip_streak >= stuck_skip_limit and dd_pct >= 2.0:
                    fails += 1
                    fail_dates.append(
                        datetime.fromtimestamp(exit_ts, tz=timezone.utc).strftime("%Y-%m-%d")
                        + ":stuck"
                    )
                    equity = ACCOUNT
                    ch_start = ACCOUNT
                    day_start = ACCOUNT
                    skip_streak = 0
                continue

        skip_streak = 0
        equity += equity * (pnl_pct / 100.0)
        day_pnl = equity - day_start
        day_pct = day_pnl / day_start * 100 if day_start else 0.0
        dd_pct = (ch_start - equity) / ch_start * 100 if ch_start else 0.0
        fr = (equity - ch_start) / ch_start * 100 if ch_start else 0.0
        when = datetime.fromtimestamp(exit_ts, tz=timezone.utc).strftime("%Y-%m-%d")

        if day_pct <= -DAILY_FAIL + 1e-9 or dd_pct >= DD_FAIL - 1e-9:
            fails += 1
            fail_dates.append(when + (":daily" if day_pct <= -DAILY_FAIL else ":dd"))
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
    pr = passes / attempts if attempts else 0.0
    return {
        "passes": passes,
        "fails": fails,
        "attempts": attempts,
        "pass_rate": round(pr, 4),
        "pass_dates": pass_dates,
        "fail_dates": fail_dates,
        "skipped": skipped,
        "ok": pr >= 0.80 and passes >= 5 and attempts >= 8,
        "final_equity": round(equity, 2),
    }


def rolling_start_pass_rate(trades, risk_pct: float, starts_every_n_trades: int = 15):
    """
    From many start indices, run one-life challenge (no rebuy) until pass/fail/end.
    Pass rate among starts that resolve (pass or fail).
    """
    if len(trades) < 40:
        return {"resolved": 0, "wins": 0, "pass_rate": 0.0}

    wins = losses = 0
    for start in range(0, len(trades) - 25, starts_every_n_trades):
        equity = ACCOUNT
        ch_start = ACCOUNT
        day = None
        day_start = ACCOUNT
        resolved = None
        for exit_ts, entry_ts, pnl_pct, s, dkey in trades[start:]:
            if day != dkey:
                day = dkey
                day_start = equity
            # gate
            day_pct = (equity - day_start) / day_start * 100 if day_start else 0.0
            if day_pct - risk_pct <= -DAILY_FAIL + 1e-9:
                continue
            after = equity * (1 - risk_pct / 100)
            if (ch_start - after) / ch_start * 100 >= DD_FAIL - 1e-9:
                continue
            equity += equity * (pnl_pct / 100.0)
            day_pct = (equity - day_start) / day_start * 100 if day_start else 0.0
            dd_pct = (ch_start - equity) / ch_start * 100 if ch_start else 0.0
            fr = (equity - ch_start) / ch_start * 100 if ch_start else 0.0
            if day_pct <= -DAILY_FAIL + 1e-9 or dd_pct >= DD_FAIL - 1e-9:
                resolved = False
                break
            if fr >= PASS_PCT - 1e-9:
                resolved = True
                break
        if resolved is True:
            wins += 1
        elif resolved is False:
            losses += 1
    resolved_n = wins + losses
    return {
        "resolved": resolved_n,
        "wins": wins,
        "losses": losses,
        "pass_rate": round(wins / resolved_n, 4) if resolved_n else 0.0,
    }


def split_trades(trades, ts_cut):
    a = [t for t in trades if t[0] < ts_cut]
    b = [t for t in trades if t[0] >= ts_cut]
    return a, b


def main():
    t0 = time.time()
    candles, _ = load_bars(365)
    ts, o, h, l, c = to_arrays(candles)
    base = build_base(ts, o, h, l, c)
    # Holdout: last ~90 days
    ts_cut = float(ts[int(len(ts) * 0.75)])
    cut_human = datetime.fromtimestamp(ts_cut, tz=timezone.utc).strftime("%Y-%m-%d")
    print(f"Holdout cut @ {cut_human} (last 25% of bars)", flush=True)

    session_sets = [
        {"ASIA", "LNDN"},
        {"LNDN", "NYAM"},
        {"ASIA", "LNDN", "NYPM"},
        {"LNDN"},
        {"NYAM", "NYPM"},
        {"ASIA", "LNDN", "NYAM", "NYPM"},
    ]

    # Higher-quality / prop-oriented families + params
    families = [
        ("sr_bounce", {"tol": 0.001}),
        ("sr_bounce", {"tol": 0.0015}),
        ("sr_bounce", {"tol": 0.002}),
        ("ema_pullback", {}),
        ("ema_rsi_bb", {}),
        ("bb_bounce", {"trend_filter": True}),
        ("bb_bounce", {"trend_filter": False}),
        ("rsi_trend", {}),
        ("rsi_mr", {"rsi_os": 25, "rsi_ob": 75}),
        ("rsi_mr", {"rsi_os": 30, "rsi_ob": 70}),
        ("smc_fvg", {"trend_filter": True}),
        ("smc_fvg", {"trend_filter": False}),
        ("ema_cross", {}),
        ("sr_break", {}),
        ("bb_break", {}),
    ]

    sides_grid = ["both", "short", "long"]
    # Phase-1 coarse grid
    risk_grid = [0.5, 0.75, 1.0]
    tp_grid = [1.0, 1.25, 1.5]
    sl_grid = [0.003, 0.005]
    cd_grid = [12, 24]
    mxd_grid = [1, 2]

    results = []
    best = None
    tested = 0
    hits = []

    for fam, base_params in families:
        for sess, sides in itertools.product(session_sets, sides_grid):
            params = {**base_params, "sides": sides, "rsi_os": base_params.get("rsi_os", 30),
                      "rsi_ob": base_params.get("rsi_ob", 70),
                      "trend_filter": base_params.get("trend_filter", False),
                      "tol": base_params.get("tol", 0.0015)}
            ctx = ctx_with_sessions(base, sess)
            sig = gen_signals(fam, params, ctx)
            nsig = int(np.count_nonzero(sig))
            if nsig < 40:
                continue

            # subsample risk grid — full for promising signal density
            for risk, tp, slp, cd, mxd in itertools.product(
                risk_grid, tp_grid, sl_grid, cd_grid, mxd_grid
            ):
                if risk >= 0.8 and mxd >= 2 and cd <= 8:
                    continue
                trades = simulate_trades(
                    sig, ts, o, h, l, c, ctx["day_id"],
                    risk_pct=risk, tp_rr=tp, sl_atr_mult=None, sl_pct=slp,
                    cooldown_bars=cd, max_trades_per_day=mxd, use_gates=True,
                )
                if len(trades) < 40:
                    continue
                fit = prop_fitness_v2(trades, risk_pct=risk, use_gates=True)
                tested += 1
                if fit["attempts"] < 6:
                    continue
                roll = rolling_start_pass_rate(trades, risk_pct=risk)
                tr_a, tr_b = split_trades(trades, ts_cut)
                fit_a = prop_fitness_v2(tr_a, risk_pct=risk, use_gates=True)
                fit_b = prop_fitness_v2(tr_b, risk_pct=risk, use_gates=True)
                # Robust OK: full >=80%, holdout attempts>=3 and holdout pass_rate>=2/3, rolling resolved
                robust_ok = (
                    fit["pass_rate"] >= 0.80
                    and fit["passes"] >= 5
                    and fit["attempts"] >= 8
                    and fit_b["attempts"] >= 3
                    and fit_b["pass_rate"] >= 0.66
                    and roll["resolved"] >= 8
                    and roll["pass_rate"] >= 0.75
                )
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
                    "passes": fit["passes"],
                    "fails": fit["fails"],
                    "attempts": fit["attempts"],
                    "pass_rate": fit["pass_rate"],
                    "ok": fit["ok"],
                    "robust_ok": robust_ok,
                    "skipped": fit["skipped"],
                    "pass_dates": fit["pass_dates"],
                    "fail_dates": fit["fail_dates"][:12],
                    "rolling_pass_rate": roll["pass_rate"],
                    "rolling_resolved": roll["resolved"],
                    "rolling_wins": roll["wins"],
                    "train_pass_rate": fit_a["pass_rate"],
                    "train_attempts": fit_a["attempts"],
                    "holdout_pass_rate": fit_b["pass_rate"],
                    "holdout_attempts": fit_b["attempts"],
                    "holdout_passes": fit_b["passes"],
                    "holdout_fails": fit_b["fails"],
                }
                score = (
                    1 if robust_ok else 0,
                    min(fit["pass_rate"], fit_b["pass_rate"] if fit_b["attempts"] >= 3 else 0),
                    fit["pass_rate"],
                    roll["pass_rate"],
                    fit["passes"],
                    -fit["fails"],
                )
                results.append(row)
                if best is None or score > best.get("_score", (-1,)):
                    best = {**row, "_score": score}
                    print(
                        f"BEST pr={fit['pass_rate']:.1%} hold={fit_b['pass_rate']:.1%} "
                        f"roll={roll['pass_rate']:.1%} P={fit['passes']} F={fit['fails']} "
                        f"{fam} {sides} risk={risk} tp={tp} sl={slp} sess={sorted(sess)} "
                        f"robust={robust_ok} [{time.time()-t0:.0f}s]",
                        flush=True,
                    )
                if robust_ok:
                    hits.append(row)
                    print(
                        "*** ROBUST HIT ***",
                        json.dumps(
                            {
                                k: row[k]
                                for k in (
                                    "family",
                                    "pass_rate",
                                    "holdout_pass_rate",
                                    "rolling_pass_rate",
                                    "passes",
                                    "fails",
                                    "risk_pct",
                                    "tp_rr",
                                    "params",
                                )
                            },
                            default=str,
                        ),
                        flush=True,
                    )

                if tested % 300 == 0:
                    print(
                        f"… tested={tested} kept={len(results)} "
                        f"best={best['pass_rate'] if best else 0:.1%} "
                        f"hits={len(hits)} [{time.time()-t0:.0f}s]",
                        flush=True,
                    )

    results.sort(
        key=lambda r: (r["pass_rate"], r.get("rolling_pass_rate", 0), r["passes"], -r["fails"]),
        reverse=True,
    )
    out = {
        "goal": "pass_rate>=80% and multiple payouts",
        "tested": tested,
        "elapsed_sec": round(time.time() - t0, 1),
        "best": best,
        "hits": hits[:20],
        "top": results[:40],
    }
    OUT.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nSaved {OUT} tested={tested} hits={len(hits)}", flush=True)
    if best:
        print("BEST:", json.dumps(best, indent=2, default=str)[:2500], flush=True)


if __name__ == "__main__":
    main()
