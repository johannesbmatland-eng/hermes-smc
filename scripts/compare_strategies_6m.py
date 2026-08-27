#!/usr/bin/env python3
"""
Compare distinct strategy variants over 6m and print monthly breakdowns.
Prop caps: daily ≤2%, MDD ≤6%.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.tune_6m import build_ov, load_cached_bars, run_bt  # noqa: E402

DAILY = 2.0
DD = 6.0


def variants():
    """Named strategy ideas — deliberately different, not a fine grid."""
    out = []

    def add(name, **kw):
        ov = build_ov(**kw)
        ov["_name"] = name
        out.append(ov)

    # Current prop baseline (yaml)
    add(
        "prop_short_asia_lndn_1.15R",
        sessions=["ASIA", "LNDN"],
        sides=["short"],
        mode="fixed_tp",
        tp=1.15,
        be=0.55,
        trail=1.0,
        long_max=55,
        short_min=45,
        risk=1.25,
        cd=150,
        lookback=2,
        sl_buffer=0.0003,
        trend_method="ema_majority",
        engulf_ratio=1.25,
    )
    # Slightly tighter TP / lower risk (higher WR refine)
    add(
        "prop_short_1.1R_risk1.2",
        sessions=["ASIA", "LNDN"],
        sides=["short"],
        mode="fixed_tp",
        tp=1.1,
        be=0.55,
        trail=1.0,
        long_max=55,
        short_min=45,
        risk=1.2,
        cd=150,
        lookback=2,
        sl_buffer=0.0003,
        trend_method="ema_majority",
        engulf_ratio=1.25,
    )
    # London only (fewer trades, maybe cleaner)
    add(
        "short_lndn_only_1.25R",
        sessions=["LNDN"],
        sides=["short"],
        mode="fixed_tp",
        tp=1.25,
        be=0.55,
        trail=1.0,
        long_max=55,
        short_min=45,
        risk=1.25,
        cd=120,
        lookback=2,
        sl_buffer=0.0003,
        trend_method="ema_majority",
        engulf_ratio=1.25,
    )
    # Add NYPM (more volume)
    add(
        "short_asia_lndn_nypm_1.15R",
        sessions=["ASIA", "LNDN", "NYPM"],
        sides=["short"],
        mode="fixed_tp",
        tp=1.15,
        be=0.55,
        trail=1.0,
        long_max=55,
        short_min=45,
        risk=1.15,
        cd=180,
        lookback=2,
        sl_buffer=0.0003,
        trend_method="ema_majority",
        engulf_ratio=1.25,
    )
    # Both sides with stricter RSI
    add(
        "both_sides_strict_rsi",
        sessions=["ASIA", "LNDN"],
        sides=["long", "short"],
        mode="fixed_tp",
        tp=1.15,
        be=0.55,
        trail=1.0,
        long_max=50,
        short_min=50,
        risk=1.0,
        cd=180,
        lookback=2,
        sl_buffer=0.0003,
        trend_method="ema_majority",
        engulf_ratio=1.25,
        rsi_band=(35, 65),
    )
    # Long-only counter-test
    add(
        "long_only_asia_lndn",
        sessions=["ASIA", "LNDN"],
        sides=["long"],
        mode="fixed_tp",
        tp=1.15,
        be=0.55,
        trail=1.0,
        long_max=55,
        short_min=45,
        risk=1.0,
        cd=150,
        lookback=2,
        sl_buffer=0.0003,
        trend_method="ema_majority",
        engulf_ratio=1.25,
    )
    # BE+trail exit style
    add(
        "short_be_trail",
        sessions=["ASIA", "LNDN"],
        sides=["short"],
        mode="be_trail",
        tp=2.0,
        be=0.75,
        trail=0.4,
        long_max=55,
        short_min=45,
        risk=1.0,
        cd=150,
        lookback=2,
        sl_buffer=0.0003,
        trend_method="ema_majority",
        engulf_ratio=1.25,
    )
    # Stronger engulf quality
    add(
        "short_strong_engulf_1.5",
        sessions=["ASIA", "LNDN"],
        sides=["short"],
        mode="fixed_tp",
        tp=1.2,
        be=0.55,
        trail=1.0,
        long_max=55,
        short_min=45,
        risk=1.35,
        cd=120,
        lookback=2,
        sl_buffer=0.0003,
        trend_method="ema_majority",
        engulf_ratio=1.5,
    )
    # HTF trend filter only
    add(
        "short_ema_1h_15m",
        sessions=["ASIA", "LNDN"],
        sides=["short"],
        mode="fixed_tp",
        tp=1.15,
        be=0.55,
        trail=1.0,
        long_max=55,
        short_min=45,
        risk=1.25,
        cd=150,
        lookback=2,
        sl_buffer=0.0003,
        trend_method="ema_1h_15m",
        engulf_ratio=1.25,
    )
    # Full sessions short, lower risk
    add(
        "short_all_sessions_low_risk",
        sessions=["ASIA", "LNDN", "NYAM", "NYPM"],
        sides=["short"],
        mode="fixed_tp",
        tp=1.15,
        be=0.55,
        trail=1.0,
        long_max=55,
        short_min=45,
        risk=0.85,
        cd=120,
        lookback=2,
        sl_buffer=0.0003,
        trend_method="ema_majority",
        engulf_ratio=1.25,
    )
    # Higher RR fewer wins
    add(
        "short_2R_fixed",
        sessions=["ASIA", "LNDN"],
        sides=["short"],
        mode="fixed_tp",
        tp=2.0,
        be=0.75,
        trail=1.0,
        long_max=55,
        short_min=45,
        risk=1.0,
        cd=150,
        lookback=2,
        sl_buffer=0.0003,
        trend_method="ema_majority",
        engulf_ratio=1.25,
    )
    # Wider RSI short bias
    add(
        "short_rsi40_tp1.25",
        sessions=["ASIA", "LNDN"],
        sides=["short"],
        mode="fixed_tp",
        tp=1.25,
        be=0.55,
        trail=1.0,
        long_max=60,
        short_min=40,
        risk=1.25,
        cd=150,
        lookback=2,
        sl_buffer=0.0003,
        trend_method="ema_majority",
        engulf_ratio=1.25,
    )
    return out


def rank(r):
    ok = 1 if r.get("risk_ok") and r["trades"] >= 20 else 0
    # Prefer consistent months: penalize negative months
    months = r.get("months") or []
    neg = sum(1 for m in months if m.get("pct", 0) < 0)
    return (
        ok,
        r["per_month_simple_pct"] if ok else -999,
        -neg,
        r["win_rate_pct"],
        r.get("profit_factor") or 0,
        -r["max_dd_pct"],
    )


def print_months(r):
    print("  month       pct    pnl     n   wr%", flush=True)
    for m in r.get("months") or []:
        print(
            f"  {m['month']}  {m['pct']:+6.2f}%  ${m['pnl']:>8.0f}  "
            f"{m.get('trades', '?'):>3}  {m.get('win_rate_pct', 0):5.1f}%",
            flush=True,
        )


def main():
    b5, b15, b1h, months = load_cached_bars()
    t0 = time.time()
    cfgs = variants()
    print(f"Comparing {len(cfgs)} strategy variants on ~{months:.0f}m…", flush=True)
    results = []
    for i, ov in enumerate(cfgs):
        name = ov.get("_name", f"v{i}")
        print(f"\n[{i+1}/{len(cfgs)}] {name} …", flush=True)
        r = run_bt(b5, b15, b1h, ov, months)
        r["name"] = name
        results.append(r)
        print(
            f"  wr={r['win_rate_pct']:.1f}% mo={r['per_month_simple_pct']:+.2f}% "
            f"total={r['total_account_pct']:+.2f}% dd={r['max_dd_pct']:.2f}% "
            f"day={r['worst_day_pct']:.2f}% n={r['trades']} pf={r['profit_factor']} "
            f"risk_ok={r['risk_ok']} [{time.time()-t0:.0f}s]",
            flush=True,
        )
        print_months(r)

    ranked = sorted(results, key=rank, reverse=True)
    best = ranked[0]
    out = {
        "caps": {"max_daily_loss_pct": DAILY, "max_dd_pct": DD, "period_months": months},
        "elapsed_sec": round(time.time() - t0, 1),
        "best_name": best["name"],
        "ranked": [],
    }
    for r in ranked:
        row = {k: r[k] for k in r if k != "overrides"}
        row["overrides"] = r["overrides"]
        out["ranked"].append(row)

    path = ROOT / "data" / "strategy_compare_6m.json"
    path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\n=== BEST: {best['name']} ===", flush=True)
    print(
        f"wr={best['win_rate_pct']}% mo={best['per_month_simple_pct']}% "
        f"dd={best['max_dd_pct']}% day={best['worst_day_pct']}% "
        f"n={best['trades']} risk_ok={best['risk_ok']}",
        flush=True,
    )
    print_months(best)
    print(f"Saved {path}", flush=True)


if __name__ == "__main__":
    main()
