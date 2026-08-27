#!/usr/bin/env python3
"""
Optimize strategies for Starter prop with MAX 30 DAYS to hit +10%.
Fail reasons: daily 3%, DD 6%, stuck near DD, OR time > 30d.
Target: pass_rate >= 80% with multiple passes.
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

OUT = ROOT / "data" / "prop_30d_search.json"
MAX_DAYS = 30.0


def challenge_30d(trades, risk_pct: float, max_days: float = MAX_DAYS, use_gates: bool = True):
    if len(trades) < 25:
        return {"passes": 0, "fails": 0, "attempts": 0, "pass_rate": 0.0, "ok": False}

    equity = ACCOUNT
    ch_start = ACCOUNT
    ch_start_ts = None
    day = None
    day_start = ACCOUNT
    passes = fails = skip_streak = 0
    events = []
    fail_reasons: dict[str, int] = {}

    def fail(reason, ts, fr=None):
        nonlocal equity, ch_start, ch_start_ts, day_start, skip_streak, fails
        fails += 1
        fail_reasons[reason] = fail_reasons.get(reason, 0) + 1
        when = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        days = (ts - ch_start_ts) / 86400 if ch_start_ts else None
        events.append(
            {
                "type": "FAIL",
                "reason": reason,
                "when": when,
                "days": round(days, 1) if days is not None else None,
                "from_start_pct": round(fr, 2) if fr is not None else None,
            }
        )
        equity = ACCOUNT
        ch_start = ACCOUNT
        ch_start_ts = ts
        day_start = ACCOUNT
        skip_streak = 0

    for exit_ts, entry_ts, pnl_pct, s, dkey in trades:
        if ch_start_ts is None:
            ch_start_ts = entry_ts

        # Time limit at entry
        if ch_start_ts is not None and (entry_ts - ch_start_ts) / 86400.0 > max_days:
            fr = (equity - ch_start) / ch_start * 100 if ch_start else 0.0
            fail("time_limit", entry_ts, fr)

        if day != dkey:
            day = dkey
            day_start = equity

        day_pct = (equity - day_start) / day_start * 100 if day_start else 0.0
        dd_pct = (ch_start - equity) / ch_start * 100 if ch_start else 0.0

        if use_gates:
            block = day_pct - risk_pct <= -DAILY_FAIL + 1e-9
            if not block:
                after = equity * (1 - risk_pct / 100)
                if (ch_start - after) / ch_start * 100 >= DD_FAIL - 1e-9:
                    block = True
            if block:
                skip_streak += 1
                if skip_streak >= 25 and dd_pct >= 2.0:
                    fail("stuck", exit_ts, (equity - ch_start) / ch_start * 100)
                continue

        skip_streak = 0
        if ch_start_ts is None:
            ch_start_ts = entry_ts

        equity += equity * (pnl_pct / 100.0)
        day_pct = (equity - day_start) / day_start * 100 if day_start else 0.0
        dd_pct = (ch_start - equity) / ch_start * 100 if ch_start else 0.0
        fr = (equity - ch_start) / ch_start * 100 if ch_start else 0.0
        days = (exit_ts - ch_start_ts) / 86400.0 if ch_start_ts else 0.0
        when = datetime.fromtimestamp(exit_ts, tz=timezone.utc).strftime("%Y-%m-%d")

        if days > max_days and fr < PASS_PCT:
            fail("time_limit", exit_ts, fr)
            continue
        if day_pct <= -DAILY_FAIL + 1e-9:
            fail("daily", exit_ts, fr)
            continue
        if dd_pct >= DD_FAIL - 1e-9:
            fail("dd", exit_ts, fr)
            continue
        if fr >= PASS_PCT - 1e-9:
            passes += 1
            events.append(
                {
                    "type": "PASS",
                    "when": when,
                    "days": round(days, 1),
                    "from_start_pct": round(fr, 2),
                }
            )
            equity = ACCOUNT
            ch_start = ACCOUNT
            ch_start_ts = exit_ts
            day_start = ACCOUNT

    attempts = passes + fails
    pr = passes / attempts if attempts else 0.0
    pass_days = [e["days"] for e in events if e["type"] == "PASS" and e.get("days") is not None]
    return {
        "passes": passes,
        "fails": fails,
        "attempts": attempts,
        "pass_rate": round(pr, 4),
        "ok": pr >= 0.80 and passes >= 5 and attempts >= 8,
        "pass_days": pass_days,
        "median_pass_days": float(np.median(pass_days)) if pass_days else None,
        "fail_reasons": fail_reasons,
        "events": events,
    }


def holdout_30d(trades, risk, ts_cut):
    a = [t for t in trades if t[0] < ts_cut]
    b = [t for t in trades if t[0] >= ts_cut]
    return challenge_30d(a, risk), challenge_30d(b, risk)


def gen_confluence(ctx, sides="both"):
    c, o = ctx["c"], ctx["o"]
    h, l = ctx["h"], ctx["l"]
    sess, ema_f, ema_s, ema_t = ctx["sess"], ctx["ema_fast"], ctx["ema_slow"], ctx["ema_trend"]
    rsi_v, bb = ctx["rsi"], ctx["bb"]
    bb_m = bb[0]
    n = len(c)
    side = np.zeros(n, dtype=np.int8)
    allow_l = sides in ("both", "long")
    allow_s = sides in ("both", "short")
    for i in range(80, n - 1):
        if not sess[i] or np.isnan(rsi_v[i]) or np.isnan(bb_m[i]) or np.isnan(ema_t[i]):
            continue
        up = c[i] > ema_t[i] and ema_f[i] > ema_s[i]
        dn = c[i] < ema_t[i] and ema_f[i] < ema_s[i]
        if allow_l and up and 35 <= rsi_v[i] <= 55 and l[i] <= bb_m[i] <= h[i] and c[i] > o[i] and c[i] > ema_f[i]:
            side[i] = 1
        elif allow_s and dn and 45 <= rsi_v[i] <= 65 and l[i] <= bb_m[i] <= h[i] and c[i] < o[i] and c[i] < ema_f[i]:
            side[i] = -1
    return side


def gen_ema_cross_rsi(ctx, sides="both"):
    base = gen_signals("ema_cross", {"sides": sides}, ctx)
    rsi_v = ctx["rsi"]
    out = base.copy()
    for i in range(len(out)):
        if out[i] == 0 or np.isnan(rsi_v[i]):
            continue
        if out[i] > 0 and not (40 <= rsi_v[i] <= 70):
            out[i] = 0
        if out[i] < 0 and not (30 <= rsi_v[i] <= 60):
            out[i] = 0
    return out


def main():
    t0 = time.time()
    candles, _ = load_bars(365)
    ts, o, h, l, c = to_arrays(candles)
    base = build_base(ts, o, h, l, c)
    ts_cut = float(ts[int(len(ts) * 0.75)])
    print(f"30d-fitness search | holdout cut {datetime.fromtimestamp(ts_cut, tz=timezone.utc).date()}", flush=True)

    # Broader session coverage for faster +10%
    seeds = []
    families = [
        ("ema_cross", {}),
        ("ema_pullback", {}),
        ("ema_rsi_bb", {}),
        ("bb_bounce", {"trend_filter": True}),
        ("sr_bounce", {"tol": 0.0015}),
        ("smc_fvg", {"trend_filter": True}),
        ("rsi_trend", {}),
        ("rsi_mr", {"rsi_os": 30, "rsi_ob": 70}),
        ("sr_break", {}),
        ("bb_break", {}),
    ]
    sessions = [
        {"LNDN"},
        {"LNDN", "NYAM"},
        {"NYAM", "NYPM"},
        {"ASIA", "LNDN"},
        {"ASIA", "LNDN", "NYAM", "NYPM"},
        {"LNDN", "NYAM", "NYPM"},
    ]
    sides_l = ["both", "short", "long"]

    signal_cache = []
    for fam, bp in families:
        for sess, sides in itertools.product(sessions, sides_l):
            params = {
                "sides": sides,
                "tol": bp.get("tol", 0.0015),
                "trend_filter": bp.get("trend_filter", False),
                "rsi_os": bp.get("rsi_os", 30),
                "rsi_ob": bp.get("rsi_ob", 70),
            }
            ctx = ctx_with_sessions(base, sess)
            sig = gen_signals(fam, params, ctx)
            if np.count_nonzero(sig) >= 40:
                signal_cache.append((fam, params, sess, sig, ctx))

    for sess, sides in itertools.product(
        [{"LNDN"}, {"LNDN", "NYAM"}, {"NYAM", "NYPM"}, {"ASIA", "LNDN", "NYAM", "NYPM"}],
        sides_l,
    ):
        ctx = ctx_with_sessions(base, sess)
        signal_cache.append(("confluence", {"sides": sides}, sess, gen_confluence(ctx, sides), ctx))
        signal_cache.append(("ema_cross_rsi", {"sides": sides}, sess, gen_ema_cross_rsi(ctx, sides), ctx))

    print(f"Signal sets: {len(signal_cache)}", flush=True)

    # Aggressive enough to hit +10% in 30d, still under 3% daily
    risk_grid = [1.0, 1.25, 1.5, 1.75, 2.0]
    tp_grid = [1.0, 1.25, 1.5, 1.75]
    sl_grid = [0.0025, 0.0035, 0.005]
    cd_grid = [6, 12, 18]
    mxd_grid = [2, 3]

    results = []
    hits = []
    best = None
    tested = 0

    for fam, params, sess, sig, ctx in signal_cache:
        for risk, tp, slp, cd, mxd in itertools.product(risk_grid, tp_grid, sl_grid, cd_grid, mxd_grid):
            # Can't risk more than daily cap allows with headroom
            if risk > 2.5:
                continue
            if risk >= 1.75 and mxd >= 4:
                continue
            trades = simulate_trades(
                sig, ts, o, h, l, c, ctx["day_id"],
                risk_pct=risk, tp_rr=tp, sl_atr_mult=None, sl_pct=slp,
                cooldown_bars=cd, max_trades_per_day=mxd, use_gates=True,
            )
            tested += 1
            if len(trades) < 40:
                continue
            fit = challenge_30d(trades, risk)
            if fit["attempts"] < 6:
                continue
            fit_a, fit_b = holdout_30d(trades, risk, ts_cut)
            robust = (
                fit["ok"]
                and fit_b["attempts"] >= 3
                and fit_b["pass_rate"] >= 0.66
            )
            row = {
                "family": fam,
                "params": {**params, "sessions": sorted(sess)},
                "risk_pct": risk,
                "tp_rr": tp,
                "sl_pct": slp,
                "cooldown_bars": cd,
                "max_day": mxd,
                "n_trades": len(trades),
                "passes": fit["passes"],
                "fails": fit["fails"],
                "attempts": fit["attempts"],
                "pass_rate": fit["pass_rate"],
                "ok": fit["ok"],
                "robust_ok": robust,
                "median_pass_days": fit["median_pass_days"],
                "pass_days": fit["pass_days"],
                "fail_reasons": fit["fail_reasons"],
                "events": fit["events"],
                "holdout_pass_rate": fit_b["pass_rate"],
                "holdout_attempts": fit_b["attempts"],
                "holdout_passes": fit_b["passes"],
                "train_pass_rate": fit_a["pass_rate"],
            }
            results.append(row)
            score = (
                1 if robust else 0,
                1 if fit["ok"] else 0,
                fit["pass_rate"],
                fit_b["pass_rate"] if fit_b["attempts"] >= 3 else -1,
                fit["passes"],
                -fit["fails"],
                -(fit["median_pass_days"] or 99),
            )
            if best is None or score > best.get("_score", (-1,)):
                best = {**row, "_score": score}
                print(
                    f"BEST 30d-pr={fit['pass_rate']:.1%} hold={fit_b['pass_rate']:.1%} "
                    f"P={fit['passes']} F={fit['fails']} medDays={fit['median_pass_days']} "
                    f"{fam} {params.get('sides')} risk={risk} tp={tp} sl={slp} cd={cd} mxd={mxd} "
                    f"sess={sorted(sess)} fails={fit['fail_reasons']} "
                    f"ok={fit['ok']} robust={robust} [{time.time()-t0:.0f}s]",
                    flush=True,
                )
            if fit["ok"]:
                hits.append(row)
                tag = "ROBUST80" if robust else "HIT80"
                print(f"*** {tag} *** pr={fit['pass_rate']:.1%} P={fit['passes']} F={fit['fails']} {fam}", flush=True)
            if tested % 800 == 0:
                print(
                    f"… tested={tested} kept={len(results)} best={best['pass_rate'] if best else 0:.1%} "
                    f"hits={len(hits)} [{time.time()-t0:.0f}s]",
                    flush=True,
                )

    results.sort(
        key=lambda r: (
            1 if r.get("robust_ok") else 0,
            1 if r.get("ok") else 0,
            r["pass_rate"],
            r.get("holdout_pass_rate", 0),
            r["passes"],
            -r["fails"],
        ),
        reverse=True,
    )
    out = {
        "goal": "pass_rate>=80% within 30 calendar days",
        "rules": {"pass": 10, "daily": 3, "dd": 6, "max_days": 30},
        "tested": tested,
        "elapsed_sec": round(time.time() - t0, 1),
        "hits80": [r for r in hits if r.get("ok")][:25],
        "robust_hits": [r for r in hits if r.get("robust_ok")][:15],
        "best": {k: v for k, v in (best or {}).items() if k != "_score"},
        "top": results[:40],
        "near70": [r for r in results if r["pass_rate"] >= 0.70][:20],
    }
    OUT.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nSaved {OUT} tested={tested} hits80={len(out['hits80'])} robust={len(out['robust_hits'])}", flush=True)
    if best:
        print("BEST:", json.dumps(out["best"], indent=2, default=str)[:3000], flush=True)


if __name__ == "__main__":
    main()
