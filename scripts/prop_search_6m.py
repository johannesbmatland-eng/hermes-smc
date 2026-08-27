#!/usr/bin/env python3
"""Prop-firm oriented search: hard DD/daily caps, best effort on profit/WR."""

from __future__ import annotations

import copy
import json
import sys
import time

sys.path.insert(0, ".")
from scripts.tune_6m import (  # noqa: E402
    MAX_DAILY_LOSS_PCT,
    MAX_DD_PCT,
    build_ov,
    load_cached_bars,
    run_bt,
)


def rank(r):
    risk_ok = 1 if r.get("risk_ok") else 0
    return (
        risk_ok,
        r["per_month_simple_pct"] if risk_ok else -999,
        r["win_rate_pct"],
        r.get("profit_factor") or 0,
        -r["max_dd_pct"],
        r["trades"],
    )


def main():
    b5, b15, b1h, months = load_cached_bars()
    t0 = time.time()
    results = []

    cfgs = []
    for sessions in (["ASIA", "LNDN"], ["LNDN"], ["ASIA", "LNDN", "NYPM"]):
        for sides in (["short"], ["long", "short"]):
            for tp in (1.0, 1.15, 1.25, 1.4, 1.5, 1.75, 2.0):
                for mode in ("fixed_tp", "be_trail"):
                    for risk in (0.75, 1.0, 1.25, 1.4, 1.5):
                        for cd in (150, 180, 240, 300):
                            for trend in ("ema_majority", "ema_1h_15m"):
                                for long_max, short_min in ((55, 45), (60, 40), (58, 42)):
                                    for ratio in (1.0, 1.25, 1.5):
                                        for lb in (1, 2):
                                            for slb in (0.0003, 0.0005, 0.0007):
                                                for band in (None, (40, 65)):
                                                    be = 0.5 if tp <= 1.25 else 0.75
                                                    trail = 0.35 if mode == "be_trail" else 1.0
                                                    cfgs.append(
                                                        build_ov(
                                                            sessions,
                                                            sides,
                                                            mode,
                                                            tp,
                                                            be,
                                                            trail,
                                                            long_max,
                                                            short_min,
                                                            risk,
                                                            cd,
                                                            lookback=lb,
                                                            sl_buffer=slb,
                                                            rsi_band=band,
                                                            trend_method=trend,
                                                            engulf_ratio=ratio,
                                                        )
                                                    )

    seen = set()
    uniq = []
    for ov in cfgs:
        k = json.dumps(ov, sort_keys=True, default=str)
        if k in seen:
            continue
        seen.add(k)
        uniq.append(ov)

    def prio(ov):
        s = 0
        if ov["_allowed_sides"] == ["short"]:
            s += 8
        en = tuple(w["name"] for w in ov["sessions"]["windows"] if w["enabled"])
        if en == ("ASIA", "LNDN"):
            s += 6
        if ov["exits"]["mode"] == "fixed_tp":
            s += 3
        if 1.0 <= ov["risk"]["rr_target"] <= 1.5:
            s += 4
        if 1.0 <= ov["risk"]["risk_pct_per_trade"] <= 1.4:
            s += 3
        if ov["trend_filter"]["method"] == "ema_1h_15m":
            s += 2
        if ov["entry"]["engulf_lookback"] == 2:
            s += 1
        return -s

    uniq.sort(key=prio)
    limit = 80
    print(
        f"Prop search {limit}/{len(uniq)} on {months:.0f}m | "
        f"caps daily≤{MAX_DAILY_LOSS_PCT}% DD≤{MAX_DD_PCT}%",
        flush=True,
    )

    for i, ov in enumerate(uniq[:limit]):
        r = run_bt(b5, b15, b1h, ov, months)
        results.append(r)
        if r["risk_ok"] and r["trades"] >= 20:
            print(
                f"OK wr={r['win_rate_pct']:5.1f} mo={r['per_month_simple_pct']:6.2f} "
                f"dd={r['max_dd_pct']:5.2f} day={r['worst_day_pct']:5.2f} "
                f"n={r['trades']:3d} pf={r['profit_factor']} "
                f"risk={ov['risk']['risk_pct_per_trade']} tp={ov['risk']['rr_target']} "
                f"trend={ov['trend_filter']['method']} sides={ov['_allowed_sides']} "
                f"sess={[w['name'] for w in ov['sessions']['windows'] if w['enabled']]} "
                f"mode={ov['exits']['mode']} ratio={ov['entry']['min_engulf_body_ratio']}",
                flush=True,
            )
        if i % 10 == 0:
            best = max(results, key=rank)
            print(
                f"… {i}/{limit} best_mo={best['per_month_simple_pct']} "
                f"wr={best['win_rate_pct']} dd={best['max_dd_pct']} "
                f"risk_ok={best['risk_ok']} [{time.time() - t0:.0f}s]",
                flush=True,
            )

    ok = [r for r in results if r["risk_ok"] and r["trades"] >= 20]
    ok = sorted(ok, key=rank, reverse=True)
    print(f"refine top {min(5, len(ok))} risk-ok seeds", flush=True)
    for seed in ok[:5]:
        base = seed["overrides"]
        for tp in (1.0, 1.15, 1.25, 1.35, 1.5, 1.75):
            for risk in (1.0, 1.15, 1.25, 1.35, 1.45, 1.5):
                for cd in (120, 150, 180, 240):
                    for trend in ("ema_majority", "ema_1h_15m"):
                        ov = copy.deepcopy(base)
                        ov["risk"]["rr_target"] = tp
                        ov["exits"]["tp_rr"] = tp
                        ov["risk"]["risk_pct_per_trade"] = risk
                        ov["entry"]["cooldown_seconds"] = cd
                        ov["trend_filter"]["method"] = trend
                        r = run_bt(b5, b15, b1h, ov, months)
                        results.append(r)
                        if r["risk_ok"] and r["per_month_simple_pct"] >= seed["per_month_simple_pct"] - 0.3:
                            print(
                                f"REF wr={r['win_rate_pct']:5.1f} mo={r['per_month_simple_pct']:6.2f} "
                                f"dd={r['max_dd_pct']:5.2f} day={r['worst_day_pct']:5.2f} "
                                f"n={r['trades']:3d} pf={r['profit_factor']} "
                                f"risk={risk} tp={tp} trend={trend} cd={cd}",
                                flush=True,
                            )

    ok = sorted(
        [r for r in results if r["risk_ok"] and r["trades"] >= 15],
        key=rank,
        reverse=True,
    )
    top = ok[:8] or sorted(results, key=rank, reverse=True)[:8]
    out = {
        "goal": "prop_firm_best_effort",
        "caps": {
            "max_daily_loss_pct": MAX_DAILY_LOSS_PCT,
            "max_dd_pct": MAX_DD_PCT,
            "period_months": months,
        },
        "elapsed_sec": round(time.time() - t0, 1),
        "candidates_risk_ok": len(ok),
        "top": [],
    }
    for r in top:
        row = {k: r[k] for k in r if k != "overrides"}
        row["overrides"] = r["overrides"]
        out["top"].append(row)

    path = "data/tune_results_6m.json"
    open(path, "w").write(json.dumps(out, indent=2, default=str))
    print("DONE risk_ok", len(ok), "best:", flush=True)
    print(json.dumps(out["top"][0], indent=2, default=str)[:4500], flush=True)


if __name__ == "__main__":
    main()
