#!/usr/bin/env python3
"""
Refine top prop families toward ≥80% robust pass rate.
Focus: ema_cross, sr_bounce short NY, smc_fvg, and new confluence variants.
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
    load_bars,
    to_arrays,
    build_base,
    ctx_with_sessions,
    gen_signals,
    simulate_trades,
)
from scripts.prop_multi_strategy_search_v2 import (  # noqa: E402
    prop_fitness_v2,
    rolling_start_pass_rate,
    split_trades,
)

OUT = ROOT / "data" / "prop_refine_80.json"


def eval_row(fam, params, sess, sig, ts, o, h, l, c, day_id, risk, tp, slp, cd, mxd, ts_cut):
    trades = simulate_trades(
        sig, ts, o, h, l, c, day_id,
        risk_pct=risk, tp_rr=tp, sl_atr_mult=None, sl_pct=slp,
        cooldown_bars=cd, max_trades_per_day=mxd, use_gates=True,
    )
    if len(trades) < 35:
        return None
    fit = prop_fitness_v2(trades, risk_pct=risk, use_gates=True)
    if fit["attempts"] < 6:
        return None
    roll = rolling_start_pass_rate(trades, risk_pct=risk, starts_every_n_trades=12)
    tr_a, tr_b = split_trades(trades, ts_cut)
    fit_a = prop_fitness_v2(tr_a, risk_pct=risk, use_gates=True)
    fit_b = prop_fitness_v2(tr_b, risk_pct=risk, use_gates=True)
    robust_ok = (
        fit["pass_rate"] >= 0.80
        and fit["passes"] >= 5
        and fit["attempts"] >= 8
        and fit_b["attempts"] >= 3
        and fit_b["pass_rate"] >= 0.66
        and roll["resolved"] >= 8
        and roll["pass_rate"] >= 0.75
    )
    return {
        "family": fam,
        "params": {**params, "sessions": sorted(sess)},
        "risk_pct": risk,
        "tp_rr": tp,
        "sl_pct": slp,
        "cooldown_bars": cd,
        "max_day": mxd,
        "n_signals": int(np.count_nonzero(sig)),
        "n_trades": len(trades),
        "passes": fit["passes"],
        "fails": fit["fails"],
        "attempts": fit["attempts"],
        "pass_rate": fit["pass_rate"],
        "ok": fit["ok"],
        "robust_ok": robust_ok,
        "pass_dates": fit["pass_dates"],
        "fail_dates": fit["fail_dates"][:15],
        "rolling_pass_rate": roll["pass_rate"],
        "rolling_resolved": roll["resolved"],
        "train_pass_rate": fit_a["pass_rate"],
        "holdout_pass_rate": fit_b["pass_rate"],
        "holdout_attempts": fit_b["attempts"],
        "holdout_passes": fit_b["passes"],
        "holdout_fails": fit_b["fails"],
    }


def gen_confluence(ctx, sides="both"):
    """EMA trend + RSI mid + BB touch + session — stricter combo."""
    c, o, h, l = ctx["c"], ctx["o"], ctx["h"], ctx["l"]
    sess, ema_f, ema_s, ema_t = ctx["sess"], ctx["ema_fast"], ctx["ema_slow"], ctx["ema_trend"]
    rsi_v = ctx["rsi"]
    bb_m, bb_u, bb_l = ctx["bb"]
    n = len(c)
    side = np.zeros(n, dtype=np.int8)
    allow_long = sides in ("both", "long")
    allow_short = sides in ("both", "short")
    for i in range(80, n - 1):
        if not sess[i] or np.isnan(rsi_v[i]) or np.isnan(bb_m[i]) or np.isnan(ema_t[i]):
            continue
        up = c[i] > ema_t[i] and ema_f[i] > ema_s[i]
        dn = c[i] < ema_t[i] and ema_f[i] < ema_s[i]
        if allow_long and up and 35 <= rsi_v[i] <= 55 and l[i] <= bb_m[i] <= h[i] and c[i] > o[i]:
            if c[i] > ema_f[i]:
                side[i] = 1
        elif allow_short and dn and 45 <= rsi_v[i] <= 65 and l[i] <= bb_m[i] <= h[i] and c[i] < o[i]:
            if c[i] < ema_f[i]:
                side[i] = -1
    return side


def gen_ema_cross_rsi(ctx, sides="both", rsi_lo=40, rsi_hi=60):
    """EMA cross filtered by RSI not extreme."""
    base = gen_signals("ema_cross", {"sides": sides}, ctx)
    rsi_v = ctx["rsi"]
    out = base.copy()
    for i in range(len(out)):
        if out[i] == 0 or np.isnan(rsi_v[i]):
            continue
        if out[i] > 0 and not (rsi_lo <= rsi_v[i] <= 70):
            out[i] = 0
        if out[i] < 0 and not (30 <= rsi_v[i] <= rsi_hi):
            out[i] = 0
    return out


def main():
    t0 = time.time()
    candles, _ = load_bars(365)
    ts, o, h, l, c = to_arrays(candles)
    base = build_base(ts, o, h, l, c)
    ts_cut = float(ts[int(len(ts) * 0.75)])
    print(f"Refine holdout cut {datetime.fromtimestamp(ts_cut, tz=timezone.utc).date()}", flush=True)

    jobs = []
    # Seed configs around known near-misses
    seeds = [
        ("ema_cross", {"sides": "both"}, {"LNDN"}),
        ("ema_cross", {"sides": "both"}, {"LNDN", "NYAM"}),
        ("ema_cross", {"sides": "short"}, {"LNDN"}),
        ("ema_cross", {"sides": "long"}, {"LNDN"}),
        ("sr_bounce", {"sides": "short", "tol": 0.0015}, {"NYAM", "NYPM"}),
        ("sr_bounce", {"sides": "short", "tol": 0.001}, {"NYAM", "NYPM"}),
        ("sr_bounce", {"sides": "short", "tol": 0.002}, {"NYAM", "NYPM"}),
        ("sr_bounce", {"sides": "both", "tol": 0.0015}, {"NYAM", "NYPM"}),
        ("smc_fvg", {"sides": "both", "trend_filter": True}, {"NYAM", "NYPM"}),
        ("smc_fvg", {"sides": "short", "trend_filter": True}, {"NYAM", "NYPM"}),
        ("smc_fvg", {"sides": "both", "trend_filter": True}, {"LNDN", "NYAM"}),
        ("bb_bounce", {"sides": "both", "trend_filter": True}, {"LNDN"}),
        ("bb_bounce", {"sides": "short", "trend_filter": True}, {"NYAM", "NYPM"}),
        ("ema_pullback", {"sides": "both"}, {"LNDN"}),
        ("ema_pullback", {"sides": "short"}, {"LNDN", "NYAM"}),
        ("rsi_trend", {"sides": "both"}, {"LNDN"}),
        ("ema_rsi_bb", {"sides": "both"}, {"LNDN"}),
        ("ema_rsi_bb", {"sides": "short"}, {"NYAM", "NYPM"}),
    ]

    risk_grid = [0.25, 0.35, 0.45, 0.55, 0.7, 0.85, 1.0]
    tp_grid = [0.85, 1.0, 1.15, 1.25, 1.4, 1.6, 2.0]
    sl_grid = [0.002, 0.0025, 0.0035, 0.005, 0.007]
    cd_grid = [6, 10, 16, 24, 36, 48]
    mxd_grid = [1, 2]

    results = []
    hits = []
    best = None
    tested = 0

    # Prebuild signals
    signal_cache = []
    for fam, params, sess in seeds:
        ctx = ctx_with_sessions(base, sess)
        p = {
            "sides": params.get("sides", "both"),
            "tol": params.get("tol", 0.0015),
            "trend_filter": params.get("trend_filter", False),
            "rsi_os": 30,
            "rsi_ob": 70,
        }
        sig = gen_signals(fam, p, ctx)
        signal_cache.append((fam, p, sess, sig, ctx))

    # Extra confluence signals
    for sess, sides in itertools.product(
        [{"LNDN"}, {"LNDN", "NYAM"}, {"NYAM", "NYPM"}, {"ASIA", "LNDN"}],
        ["both", "short", "long"],
    ):
        ctx = ctx_with_sessions(base, sess)
        signal_cache.append(("confluence", {"sides": sides}, sess, gen_confluence(ctx, sides), ctx))
        signal_cache.append(("ema_cross_rsi", {"sides": sides}, sess, gen_ema_cross_rsi(ctx, sides), ctx))

    print(f"Signal sets: {len(signal_cache)}", flush=True)

    for fam, params, sess, sig, ctx in signal_cache:
        nsig = int(np.count_nonzero(sig))
        if nsig < 25:
            continue
        for risk, tp, slp, cd, mxd in itertools.product(risk_grid, tp_grid, sl_grid, cd_grid, mxd_grid):
            # prune reckless
            if risk >= 0.85 and mxd >= 2 and cd <= 10:
                continue
            if risk <= 0.35 and tp >= 2.0 and slp >= 0.007:
                continue
            row = eval_row(fam, params, sess, sig, ts, o, h, l, c, ctx["day_id"], risk, tp, slp, cd, mxd, ts_cut)
            tested += 1
            if not row:
                continue
            results.append(row)
            score = (
                1 if row["robust_ok"] else 0,
                min(row["pass_rate"], row["holdout_pass_rate"] if row["holdout_attempts"] >= 3 else 0),
                row["pass_rate"],
                row["rolling_pass_rate"],
                row["passes"],
                -row["fails"],
            )
            if best is None or score > best.get("_score", (-1,)):
                best = {**row, "_score": score}
                print(
                    f"BEST pr={row['pass_rate']:.1%} hold={row['holdout_pass_rate']:.1%} "
                    f"roll={row['rolling_pass_rate']:.1%}({row['rolling_resolved']}) "
                    f"P={row['passes']} F={row['fails']} {fam} {params.get('sides')} "
                    f"risk={risk} tp={tp} sl={slp} cd={cd} sess={sorted(sess)} "
                    f"robust={row['robust_ok']} [{time.time()-t0:.0f}s]",
                    flush=True,
                )
            if row["robust_ok"]:
                hits.append(row)
                print("*** ROBUST ≥80% ***", flush=True)
                print(json.dumps(row, indent=2, default=str)[:2000], flush=True)
            if tested % 500 == 0:
                print(
                    f"… tested={tested} kept={len(results)} best={best['pass_rate'] if best else 0:.1%} "
                    f"hits={len(hits)} [{time.time()-t0:.0f}s]",
                    flush=True,
                )

    results.sort(
        key=lambda r: (
            1 if r.get("robust_ok") else 0,
            r["pass_rate"],
            r.get("holdout_pass_rate", 0),
            r["passes"],
            -r["fails"],
        ),
        reverse=True,
    )
    # Also near-80
    near = [r for r in results if r["pass_rate"] >= 0.75]
    out = {
        "goal": "robust_pass_rate>=80%",
        "tested": tested,
        "elapsed_sec": round(time.time() - t0, 1),
        "hits": hits,
        "best": {k: v for k, v in (best or {}).items() if k != "_score"},
        "near_75": near[:20],
        "top": results[:30],
    }
    OUT.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nSaved {OUT} hits={len(hits)} near75={len(near)}", flush=True)
    if best:
        print("BEST:", json.dumps(out["best"], indent=2, default=str)[:2500], flush=True)


if __name__ == "__main__":
    main()
