#!/usr/bin/env python3
"""
Fetch longer OKX history and compare short-only vs both vs long-only
with the locked prop profile (engulf 1.5, TP 1.2R, risk 1.35%).
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.backtest_3m import CACHE_DIR, fetch_ohlcv_range  # noqa: E402
from scripts.tune_6m import build_ov, run_bt  # noqa: E402


def fetch_days(days: int):
    until = datetime.now(timezone.utc)
    since = until - timedelta(days=days)
    since_ms = int(since.timestamp() * 1000)
    until_ms = int(until.timestamp() * 1000)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tag = f"okx_BTCUSDT_{days}d_{since.date()}_{until.date()}"
    print(f"Fetching/loading {days}d ({since.date()} → {until.date()})…", flush=True)
    b5 = fetch_ohlcv_range("BTC/USDT", "5m", since_ms, until_ms, CACHE_DIR / f"{tag}_5m.json", "okx")
    b15 = fetch_ohlcv_range("BTC/USDT", "15m", since_ms, until_ms, CACHE_DIR / f"{tag}_15m.json", "okx")
    b1h = fetch_ohlcv_range("BTC/USDT", "1h", since_ms, until_ms, CACHE_DIR / f"{tag}_1h.json", "okx")
    months = days / 30.44
    return b5, b15, b1h, months, tag


def market_summary(bars):
    o, c = bars[0]["close"], bars[-1]["close"]
    months = {}
    for bar in bars:
        k = datetime.fromtimestamp(bar["timestamp"], tz=timezone.utc).strftime("%Y-%m")
        if k not in months:
            months[k] = {"o": bar["open"], "c": bar["close"]}
        months[k]["c"] = bar["close"]
    return {
        "start": datetime.fromtimestamp(bars[0]["timestamp"], tz=timezone.utc).isoformat(),
        "end": datetime.fromtimestamp(bars[-1]["timestamp"], tz=timezone.utc).isoformat(),
        "btc_start": o,
        "btc_end": c,
        "btc_pct": round((c / o - 1) * 100, 2),
        "monthly_btc": [
            {
                "month": k,
                "pct": round((v["c"] / v["o"] - 1) * 100, 2),
            }
            for k, v in sorted(months.items())
        ],
    }


def ov_for(sides):
    return build_ov(
        sessions=["ASIA", "LNDN"],
        sides=sides,
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


def print_r(name, r):
    print(
        f"\n=== {name} ===\n"
        f"  wr={r['win_rate_pct']}% mo={r['per_month_simple_pct']:+.2f}% "
        f"total={r['total_account_pct']:+.2f}% dd={r['max_dd_pct']:.2f}% "
        f"day={r['worst_day_pct']:.2f}% n={r['trades']} pf={r['profit_factor']} "
        f"risk_ok={r['risk_ok']}",
        flush=True,
    )
    print("  month       bot%   n   wr%   (btc month in summary)", flush=True)
    for m in r.get("months") or []:
        print(
            f"  {m['month']}  {m['pct']:+6.2f}%  {m.get('trades',0):>3}  "
            f"{m.get('win_rate_pct',0):5.1f}%",
            flush=True,
        )


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 365
    t0 = time.time()
    b5, b15, b1h, months, tag = fetch_days(days)
    mkt = market_summary(b5)
    print(
        f"Market: BTC {mkt['btc_start']:.0f} → {mkt['btc_end']:.0f} "
        f"({mkt['btc_pct']:+.1f}%) over ~{months:.1f}m",
        flush=True,
    )
    for row in mkt["monthly_btc"]:
        print(f"  BTC {row['month']} {row['pct']:+.1f}%", flush=True)

    results = {}
    for label, sides in (
        ("short_only", ["short"]),
        ("long_only", ["long"]),
        ("both_sides", ["long", "short"]),
    ):
        print(f"\nRunning {label}…", flush=True)
        r = run_bt(b5, b15, b1h, ov_for(sides), months)
        r["name"] = label
        results[label] = r
        print_r(label, r)

    out = {
        "tag": tag,
        "days": days,
        "months": months,
        "market": mkt,
        "elapsed_sec": round(time.time() - t0, 1),
        "results": {
            k: {kk: vv for kk, vv in v.items() if kk != "overrides"}
            for k, v in results.items()
        },
    }
    path = ROOT / "data" / f"sides_compare_{days}d.json"
    path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nSaved {path} [{time.time()-t0:.0f}s]", flush=True)

    # Verdict
    short = results["short_only"]
    both = results["both_sides"]
    long = results["long_only"]
    print("\nVERDICT:", flush=True)
    print(
        f"  short mo={short['per_month_simple_pct']}% wr={short['win_rate_pct']}% "
        f"dd={short['max_dd_pct']}% risk_ok={short['risk_ok']}",
        flush=True,
    )
    print(
        f"  both  mo={both['per_month_simple_pct']}% wr={both['win_rate_pct']}% "
        f"dd={both['max_dd_pct']}% risk_ok={both['risk_ok']}",
        flush=True,
    )
    print(
        f"  long  mo={long['per_month_simple_pct']}% wr={long['win_rate_pct']}% "
        f"dd={long['max_dd_pct']}% risk_ok={long['risk_ok']}",
        flush=True,
    )


if __name__ == "__main__":
    main()
