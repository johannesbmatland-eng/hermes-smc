#!/usr/bin/env python3
"""Fast prop-oriented pick: maximize monthly return under DD/daily caps."""

from __future__ import annotations

import copy
import json
import sys
import time

sys.path.insert(0, ".")
from scripts.tune_6m import build_ov, load_cached_bars, run_bt  # noqa: E402

DAILY = 2.0
DD = 6.0


def rank(r):
    ok = 1 if r.get("risk_ok") and r["trades"] >= 25 else 0
    return (
        ok,
        r["per_month_simple_pct"] if ok else -999,
        r["win_rate_pct"],
        r.get("profit_factor") or 0,
        -r["max_dd_pct"],
    )


def main():
    b5, b15, b1h, months = load_cached_bars()
    t0 = time.time()
    results = []

    cfgs = []
    # Tight grid around the previously strong region
    for sessions in (["ASIA", "LNDN"], ["ASIA", "LNDN", "NYPM"], ["LNDN"]):
        for sides in (["short"],):
            for tp in (1.0, 1.15, 1.25, 1.35, 1.5):
                for mode in ("fixed_tp",):
                    for risk in (1.0, 1.15, 1.25, 1.35, 1.45, 1.5):
                        for cd in (120, 150, 180, 240, 300):
                            for trend in ("ema_majority", "ema_1h_15m"):
                                for long_max, short_min in ((55, 45), (60, 40)):
                                    for ratio in (1.0, 1.25):
                                        for lb in (2,):
                                            for slb in (0.0003, 0.0005, 0.0007):
                                                cfgs.append(
                                                    build_ov(
                                                        sessions,
                                                        sides,
                                                        mode,
                                                        tp,
                                                        0.55,
                                                        1.0,
                                                        long_max,
                                                        short_min,
                                                        risk,
                                                        cd,
                                                        lookback=lb,
                                                        sl_buffer=slb,
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
        if ov["trend_filter"]["method"] == "ema_majority":
            s += 5
        if tuple(w["name"] for w in ov["sessions"]["windows"] if w["enabled"]) == (
            "ASIA",
            "LNDN",
        ):
            s += 5
        if 1.15 <= ov["risk"]["rr_target"] <= 1.35:
            s += 3
        if 1.25 <= ov["risk"]["risk_pct_per_trade"] <= 1.45:
            s += 3
        return -s

    uniq.sort(key=prio)
    limit = 60
    print(f"Fast prop pick {limit}/{len(uniq)} ({months:.0f}m)", flush=True)

    for i, ov in enumerate(uniq[:limit]):
        r = run_bt(b5, b15, b1h, ov, months)
        results.append(r)
        if r["risk_ok"]:
            print(
                f"OK wr={r['win_rate_pct']:5.1f} mo={r['per_month_simple_pct']:6.2f} "
                f"dd={r['max_dd_pct']:5.2f} day={r['worst_day_pct']:5.2f} "
                f"n={r['trades']:3d} pf={r['profit_factor']} "
                f"risk={ov['risk']['risk_pct_per_trade']} tp={ov['risk']['rr_target']} "
                f"trend={ov['trend_filter']['method']} cd={ov['entry']['cooldown_seconds']} "
                f"slb={ov['risk']['sl_buffer_pct']} ratio={ov['entry']['min_engulf_body_ratio']}",
                flush=True,
            )
        if i % 8 == 0:
            best = max(results, key=rank)
            print(
                f"… {i}/{limit} best_mo={best['per_month_simple_pct']} "
                f"wr={best['win_rate_pct']} dd={best['max_dd_pct']} "
                f"ok={best['risk_ok']} [{time.time()-t0:.0f}s]",
                flush=True,
            )

    # Refine best risk-ok
    ok = sorted([r for r in results if r["risk_ok"] and r["trades"] >= 25], key=rank, reverse=True)
    print(f"refine {min(4, len(ok))} seeds", flush=True)
    for seed in ok[:4]:
        base = seed["overrides"]
        for risk in (1.2, 1.3, 1.35, 1.4, 1.45, 1.5):
            for tp in (1.1, 1.2, 1.25, 1.3, 1.4, 1.5):
                for cd in (120, 150, 180, 210):
                    ov = copy.deepcopy(base)
                    ov["risk"]["risk_pct_per_trade"] = risk
                    ov["risk"]["rr_target"] = tp
                    ov["exits"]["tp_rr"] = tp
                    ov["entry"]["cooldown_seconds"] = cd
                    r = run_bt(b5, b15, b1h, ov, months)
                    results.append(r)
                    if r["risk_ok"] and r["per_month_simple_pct"] >= 5:
                        print(
                            f"REF wr={r['win_rate_pct']:5.1f} mo={r['per_month_simple_pct']:6.2f} "
                            f"dd={r['max_dd_pct']:5.2f} day={r['worst_day_pct']:5.2f} "
                            f"n={r['trades']:3d} pf={r['profit_factor']} risk={risk} tp={tp} cd={cd}",
                            flush=True,
                        )

    ok = sorted([r for r in results if r["risk_ok"] and r["trades"] >= 20], key=rank, reverse=True)
    top = ok[:5]
    out = {
        "goal": "prop_firm_best_effort",
        "caps": {"max_daily_loss_pct": DAILY, "max_dd_pct": DD, "period_months": months},
        "elapsed_sec": round(time.time() - t0, 1),
        "top": [],
    }
    for r in top:
        row = {k: r[k] for k in r if k != "overrides"}
        row["overrides"] = r["overrides"]
        out["top"].append(row)
    open("data/tune_results_6m.json", "w").write(json.dumps(out, indent=2, default=str))
    print("BEST:", flush=True)
    print(json.dumps(out["top"][0], indent=2, default=str)[:4000], flush=True)


if __name__ == "__main__":
    main()
