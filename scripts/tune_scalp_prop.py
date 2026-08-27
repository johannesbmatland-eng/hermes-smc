#!/usr/bin/env python3
"""
Backtest/tune the scalp bot (many small trades) under prop DD rules.

Goals: ~10–20%/month, daily≤3%, MDD≤6%, with futures-like RT fee.
"""

from __future__ import annotations

import copy
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.backtest_3m import (  # noqa: E402
    CONFIG_PATH,
    CACHE_DIR,
    align_upto,
    try_exit_on_bar,
    exit_fill_price,
)
from hermes_smc.engine.analytics import enrich_trade_meta, session_from_ts  # noqa: E402
from hermes_smc.engine.paper_trading import PaperTradingEngine  # noqa: E402
from hermes_smc.engine.smc_engine import SMCConfig  # noqa: E402
from scripts.tune_6m import deep_merge, day_key  # noqa: E402

DAILY = 3.0
DD = 6.0
ACCOUNT = 100_000.0


def load_365():
    f5 = sorted(CACHE_DIR.glob("okx_BTCUSDT_365d_*_5m.json"), key=lambda p: p.stat().st_size)[-1]
    stem = f5.name.replace("_5m.json", "")
    b5 = json.loads(f5.read_text())["candles"]
    b15 = json.loads((CACHE_DIR / f"{stem}_15m.json").read_text())["candles"]
    b1h = json.loads((CACHE_DIR / f"{stem}_1h.json").read_text())["candles"]
    print(f"Cache {f5.name}: {len(b5)} bars", flush=True)
    months = (b5[-1]["timestamp"] - b5[0]["timestamp"]) / (86400 * 30.44)
    return b5, b15, b1h, months


def run_scalp(bars_5m, bars_15m, bars_1h, overrides, period_months: float):
    cfg = SMCConfig(CONFIG_PATH)
    cfg._config = deep_merge(cfg._config, overrides)
    # bar-time sessions
    sessions = dict(cfg.get("sessions", {}) or {})
    sessions["filter_entries"] = False
    cfg._config["sessions"] = sessions

    fee_rt = float(cfg.get("risk.fee_roundtrip_pct", 0.0004) or 0.0)
    daily_halt = float(cfg.get("risk.max_daily_loss_pct", DAILY))
    max_dd = float(cfg.get("risk.max_drawdown_pct", DD))
    risk_pct = float(cfg.get("risk.risk_pct_per_trade", 0.4))

    engine = PaperTradingEngine(cfg)
    pm = engine.position_manager
    pm.state_dir = None
    pm.open_positions = {}
    pm.closed_positions = []
    pm.trade_history = []
    pm.capital = ACCOUNT
    pm.initial_capital = ACCOUNT

    session_cfg = cfg.get("sessions") or {}
    enabled = {w["name"] for w in session_cfg.get("windows", []) if w.get("enabled", True)}
    cooldown = float(cfg.get("entry.cooldown_seconds", 90))
    max_open = int(cfg.get("entry.max_open_positions", 1))
    last_trade_ts = 0.0
    win_5m = 500

    equity = ACCOUNT
    peak = ACCOUNT
    max_dd_seen = 0.0
    day_start = {}
    day_pnl = {}
    halted = set()
    fee_paid = 0.0

    def on_close(pnl_gross, ts, entry, size):
        nonlocal equity, peak, max_dd_seen, fee_paid
        # futures-like RT fee on notional
        notional = abs(entry * size)
        fee = notional * fee_rt
        fee_paid += fee
        pnl = pnl_gross - fee
        equity += pnl
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100 if peak else 0.0
        max_dd_seen = max(max_dd_seen, dd)
        dk = day_key(ts)
        if dk not in day_start:
            day_start[dk] = equity - pnl
        day_pnl[dk] = day_pnl.get(dk, 0.0) + pnl
        start = day_start[dk]
        if start and (day_pnl[dk] / start * 100) <= -daily_halt:
            halted.add(dk)
        return pnl

    for i in range(max(win_5m, 120), len(bars_5m)):
        bar = bars_5m[i]
        ts = bar["timestamp"]
        c5 = bars_5m[max(0, i + 1 - win_5m) : i + 1]
        c15 = align_upto(bars_15m, ts)[-200:]
        c1h = align_upto(bars_1h, ts)[-200:]
        if len(c15) < 60 or len(c1h) < 60:
            continue

        for tid, pos in list(pm.open_positions.items()):
            reason = try_exit_on_bar(engine, pos, bar)
            if reason:
                fill = exit_fill_price(pos, reason, bar)
                exit_rsi = engine._calc_rsi(c5, int(cfg.get("rsi.period", 14)))
                closed = pm.close_position(tid, fill, reason, rsi_at_exit=exit_rsi)
                if closed:
                    closed["exit_time"] = float(ts)
                    net = on_close(
                        float(closed.get("pnl") or 0),
                        float(ts),
                        float(closed.get("entry_price") or pos["entry_price"]),
                        float(closed.get("position_size") or pos["position_size"]),
                    )
                    closed["pnl_net"] = net
                    # keep pm.capital aligned with fee-adjusted equity path
                    pm.capital = equity
                last_trade_ts = ts

        dk = day_key(ts)
        if dk in halted:
            continue
        if len(pm.open_positions) >= max_open or ts - last_trade_ts < cooldown:
            continue

        # Prop gates: another full risk must not breach daily / DD
        if dk in day_start:
            day_pct_now = day_pnl.get(dk, 0.0) / day_start[dk] * 100 if day_start[dk] else 0.0
            if day_pct_now - risk_pct < -daily_halt - 1e-9:
                halted.add(dk)
                continue
        dd_now = (peak - equity) / peak * 100 if peak else 0.0
        if dd_now + risk_pct >= max_dd - 1e-9:
            continue

        locked_ts = c5[-2]["timestamp"]
        sess = session_from_ts(locked_ts, session_cfg)
        if sess not in enabled:
            continue

        # Temporarily point capital at mark equity for sizing
        pm.capital = equity
        signal = engine.detect_entry_signal(c5, c15, c1h, candles_1m=None)
        if not signal or signal["position_size"] <= 0:
            continue

        tid = str(uuid.uuid4())
        pm.open_position(
            trade_id=tid,
            asset="BTC/USD",
            side=signal["side"],
            entry_price=signal["entry_price"],
            position_size=signal["position_size"],
            sl_price=signal["sl_price"],
            tp_price=signal["tp_price"],
            strategy_info={
                "confirmation": signal["confirmation"],
                "trend": (signal.get("trend_info") or {}).get("overall"),
                "side": signal["side"],
                "session": sess,
                "rsi_at_entry": round(float(signal["rsi"]), 2) if signal.get("rsi") is not None else None,
            },
        )
        pm.open_positions[tid]["open_time"] = float(locked_ts)
        last_trade_ts = ts

    if pm.open_positions:
        last = bars_5m[-1]
        for tid in list(pm.open_positions):
            pos = pm.open_positions[tid]
            closed = pm.close_position(tid, float(last["close"]), "end_of_backtest")
            if closed:
                closed["exit_time"] = float(last["timestamp"])
                net = on_close(
                    float(closed.get("pnl") or 0),
                    float(last["timestamp"]),
                    float(closed["entry_price"]),
                    float(closed["position_size"]),
                )
                closed["pnl_net"] = net
                pm.capital = equity

    enriched = [enrich_trade_meta(t, ACCOUNT, session_cfg) for t in pm.closed_positions]
    wins = [t for t in enriched if float(t.get("pnl_net", t.get("pnl") or 0)) > 0]
    total_pct = (equity - ACCOUNT) / ACCOUNT * 100
    per_mo = total_pct / period_months if period_months else 0.0
    wr = 100.0 * len(wins) / len(enriched) if enriched else 0.0

    worst_day = 0.0
    for dk, pnl in day_pnl.items():
        start = day_start.get(dk, ACCOUNT)
        pct = pnl / start * 100 if start else 0.0
        worst_day = min(worst_day, pct)

    months = {}
    for t in enriched:
        ts = t.get("exit_time") or t.get("open_time")
        if not ts:
            continue
        key = datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m")
        row = months.setdefault(key, {"pnl": 0.0, "trades": 0, "wins": 0})
        pnl = float(t.get("pnl_net", t.get("pnl") or 0))
        row["pnl"] += pnl
        row["trades"] += 1
        if pnl > 0:
            row["wins"] += 1
    month_rows = []
    for k, v in sorted(months.items()):
        n = v["trades"]
        month_rows.append(
            {
                "month": k,
                "pct": round(v["pnl"] / ACCOUNT * 100, 3),
                "pnl": round(v["pnl"], 2),
                "trades": n,
                "win_rate_pct": round(100.0 * v["wins"] / n, 1) if n else 0.0,
            }
        )

    risk_ok = max_dd_seen <= DD + 1e-6 and worst_day >= -(DAILY + 0.05)
    target_ok = risk_ok and 10.0 <= per_mo <= 25.0 and len(enriched) >= 100
    return {
        "trades": len(enriched),
        "wins": len(wins),
        "win_rate_pct": round(wr, 2),
        "total_account_pct": round(total_pct, 3),
        "per_month_simple_pct": round(per_mo, 3),
        "max_dd_pct": round(max_dd_seen, 3),
        "worst_day_pct": round(worst_day, 3),
        "fee_paid": round(fee_paid, 2),
        "months": month_rows,
        "risk_ok": risk_ok,
        "target_ok": target_ok,
        "overrides": overrides,
        "period_months": round(period_months, 2),
    }


def variants():
    base = {
        "entry": {"strategy": "scalp", "scalp_mode": "ema_bb", "allowed_sides": ["long", "short"]},
        "risk": {"fee_roundtrip_pct": 0.0004, "max_daily_loss_pct": 3.0, "max_drawdown_pct": 6.0},
    }
    out = []
    for mode in ("ema_bb", "ema_pullback", "bb_bounce"):
        for risk in (0.25, 0.35, 0.4, 0.5, 0.6):
            for tp in (0.8, 1.0, 1.2):
                for sl in (0.002, 0.0025, 0.0035):
                    for cd in (60, 90, 120, 180):
                        for sides in (["long", "short"], ["short"], ["long"]):
                            ov = copy.deepcopy(base)
                            ov["entry"]["scalp_mode"] = mode
                            ov["entry"]["cooldown_seconds"] = cd
                            ov["entry"]["allowed_sides"] = sides
                            ov["risk"]["risk_pct_per_trade"] = risk
                            ov["risk"]["rr_target"] = tp
                            ov["risk"]["sl_pct"] = sl
                            ov["exits"] = {"mode": "fixed_tp", "tp_rr": tp, "trail_after_be": False}
                            ov["_name"] = f"{mode}|r{risk}|tp{tp}|sl{sl}|cd{cd}|{''.join(s[0] for s in sides)}"
                            out.append(ov)
    return out


def main():
    b5, b15, b1h, months = load_365()
    t0 = time.time()
    cfgs = variants()
    # Prioritize balanced both-sides medium risk
    def prio(ov):
        s = 0
        if ov["entry"]["allowed_sides"] == ["long", "short"]:
            s += 5
        if ov["entry"]["scalp_mode"] == "ema_bb":
            s += 3
        if 0.35 <= ov["risk"]["risk_pct_per_trade"] <= 0.5:
            s += 2
        if ov["risk"]["rr_target"] == 1.0:
            s += 1
        return -s

    cfgs.sort(key=prio)
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    work = cfgs[:limit]
    print(f"Tuning {len(work)}/{len(cfgs)} scalp configs on ~{months:.1f}m…", flush=True)

    results = []
    best = None
    for i, ov in enumerate(work):
        r = run_scalp(b5, b15, b1h, ov, months)
        r["name"] = ov.get("_name")
        results.append(r)
        score = (
            1 if r["risk_ok"] else 0,
            1 if r["target_ok"] else 0,
            # prefer in-band monthly, else closest below 20 with risk_ok
            -abs(r["per_month_simple_pct"] - 15) if r["risk_ok"] else -999,
            r["per_month_simple_pct"] if r["risk_ok"] else -999,
            r["win_rate_pct"],
            r["trades"],
        )
        if best is None or score > best[0]:
            best = (score, r)
            print(
                f"BEST mo={r['per_month_simple_pct']:+.2f}% wr={r['win_rate_pct']:.1f}% "
                f"dd={r['max_dd_pct']:.2f}% day={r['worst_day_pct']:.2f}% n={r['trades']} "
                f"fees=${r['fee_paid']:.0f} risk_ok={r['risk_ok']} target={r['target_ok']} "
                f"{r['name']} [{time.time()-t0:.0f}s]",
                flush=True,
            )
            for m in r["months"]:
                print(
                    f"  {m['month']} {m['pct']:+6.2f}% n={m['trades']:3d} wr={m['win_rate_pct']:.0f}%",
                    flush=True,
                )
        if (i + 1) % 10 == 0:
            print(f"… {i+1}/{len(work)} [{time.time()-t0:.0f}s]", flush=True)

    results.sort(
        key=lambda r: (
            1 if r["risk_ok"] else 0,
            1 if r["target_ok"] else 0,
            r["per_month_simple_pct"] if r["risk_ok"] else -999,
            r["win_rate_pct"],
        ),
        reverse=True,
    )
    out = {
        "goal": "10-20%/mo under prop 3% daily / 6% DD with many small trades",
        "fee_roundtrip_pct": 0.0004,
        "elapsed_sec": round(time.time() - t0, 1),
        "best": results[0] if results else None,
        "top": results[:15],
        "hits_target": [r for r in results if r.get("target_ok")],
    }
    path = ROOT / "data" / "scalp_prop_tune.json"
    path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nSaved {path} hits={len(out['hits_target'])}", flush=True)
    if out["best"]:
        print(json.dumps({k: out["best"][k] for k in out["best"] if k != "overrides"}, indent=2, default=str)[:3500], flush=True)


if __name__ == "__main__":
    main()
