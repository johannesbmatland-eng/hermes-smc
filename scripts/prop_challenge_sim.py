#!/usr/bin/env python3
"""
Simulate FTMO-style / Norwegian Starter prop evaluation over 1y of trades.

Rules (from user's Starter plan screenshot):
  - Account: $100,000
  - Pass:   +10% from challenge start
  - Fail:   daily loss ≥ 3% (from NY day-start equity)
  - Fail:   drawdown ≥ 6% from challenge start (static)  [primary]
  - Also report trailing DD from challenge equity peak
"""

from __future__ import annotations

import copy
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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
from scripts.tune_6m import build_ov, deep_merge, day_key  # noqa: E402

# Starter plan from screenshot
ACCOUNT = 100_000.0
PASS_PCT = 10.0
DAILY_FAIL_PCT = 3.0
DD_FAIL_PCT = 6.0  # static from challenge start
FEE_USD = 800.0


def load_365():
    files = sorted(CACHE_DIR.glob("okx_BTCUSDT_365d_*_5m.json"), key=lambda p: p.stat().st_size)
    if not files:
        raise SystemExit("No 365d cache — run scripts/sides_regime_compare.py 365 first")
    f5 = files[-1]
    stem = f5.name.replace("_5m.json", "")
    b5 = json.loads(f5.read_text())["candles"]
    b15 = json.loads((CACHE_DIR / f"{stem}_15m.json").read_text())["candles"]
    b1h = json.loads((CACHE_DIR / f"{stem}_1h.json").read_text())["candles"]
    print(f"Cache {f5.name}: {len(b5)} bars", flush=True)
    return b5, b15, b1h


def locked_ov():
    """Live prop short profile, but allow trading up to challenge daily (3%)."""
    ov = build_ov(
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
    # Do NOT halt inside backtest on 2% — challenge allows 3%; we enforce challenge rules ourselves
    ov["risk"]["max_daily_loss_pct"] = 99.0
    ov["risk"]["max_drawdown_pct"] = 99.0
    ov["_max_daily_loss_pct"] = 99.0
    return ov


def collect_trades(bars_5m, bars_15m, bars_1h, overrides):
    """Same engine path as tune_6m.run_bt, but return chronological closed trades + no internal halt."""
    cfg = SMCConfig(CONFIG_PATH)
    cfg._config = deep_merge(cfg._config, overrides)
    sessions = dict(cfg.get("sessions", {}) or {})
    sessions["filter_entries"] = False
    cfg._config["sessions"] = sessions

    engine = PaperTradingEngine(cfg)
    pm = engine.position_manager
    pm.state_dir = None
    pm.open_positions = {}
    pm.closed_positions = []
    pm.trade_history = []
    initial = ACCOUNT
    pm.capital = initial
    pm.initial_capital = initial

    session_cfg = cfg.get("sessions") or {}
    enabled = {w["name"] for w in session_cfg.get("windows", []) if w.get("enabled", True)}
    allowed = set(overrides.get("_allowed_sides") or ["short"])
    cooldown = float(cfg.get("entry.cooldown_seconds", 300))
    max_open = int(cfg.get("entry.max_open_positions", 1))
    last_trade_ts = 0.0
    win_5m = 500

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
                last_trade_ts = ts

        if len(pm.open_positions) >= max_open or ts - last_trade_ts < cooldown:
            continue

        locked_ts = c5[-2]["timestamp"]
        sess = session_from_ts(locked_ts, session_cfg)
        if sess not in enabled:
            continue

        signal = engine.detect_entry_signal(c5, c15, c1h, candles_1m=None)
        if not signal or signal["position_size"] <= 0:
            continue
        side = signal.get("side", "long")
        if side not in allowed:
            continue

        tid = str(uuid.uuid4())
        pm.open_position(
            trade_id=tid,
            asset="BTC/USD",
            side=side,
            entry_price=signal["entry_price"],
            position_size=signal["position_size"],
            sl_price=signal["sl_price"],
            tp_price=signal["tp_price"],
            strategy_info={
                "confirmation": signal["confirmation"],
                "trend": signal["trend_info"]["overall"],
                "side": side,
                "session": sess,
            },
        )
        pm.open_positions[tid]["open_time"] = float(locked_ts)
        last_trade_ts = ts

    if pm.open_positions:
        last = bars_5m[-1]
        for tid in list(pm.open_positions):
            exit_rsi = engine._calc_rsi(bars_5m[-win_5m:], int(cfg.get("rsi.period", 14)))
            closed = pm.close_position(
                tid, float(last["close"]), "end_of_backtest", rsi_at_exit=exit_rsi
            )
            if closed:
                closed["exit_time"] = float(last["timestamp"])

    trades = sorted(pm.closed_positions, key=lambda t: float(t.get("exit_time") or 0))
    return trades


def simulate_challenges(trades, dd_mode: str = "static"):
    """
    Walk trades. Each challenge starts at current equity as baseline (100k first).
    Pass at +PASS_PCT. Fail on daily or DD breach.
    dd_mode: 'static' = from challenge start; 'trailing' = from challenge peak.
    """
    equity = ACCOUNT
    challenge_start = ACCOUNT
    challenge_peak = ACCOUNT
    challenge_num = 1
    passes = []
    events = []

    day_start_eq = None
    cur_day = None
    failed = None

    for t in trades:
        if failed:
            break
        ts = float(t.get("exit_time") or t.get("open_time") or 0)
        pnl = float(t.get("pnl") or 0)
        dk = day_key(ts)

        if cur_day != dk:
            cur_day = dk
            day_start_eq = equity

        equity += pnl
        challenge_peak = max(challenge_peak, equity)

        day_pct = ((equity - day_start_eq) / day_start_eq * 100) if day_start_eq else 0.0
        if dd_mode == "trailing":
            dd_pct = (challenge_peak - equity) / challenge_peak * 100 if challenge_peak else 0.0
        else:
            dd_pct = (challenge_start - equity) / challenge_start * 100 if challenge_start else 0.0
        from_start = (equity - challenge_start) / challenge_start * 100 if challenge_start else 0.0

        when = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

        # Fail checks first (same bar: if both pass and fail, fail wins if breached)
        if day_pct <= -DAILY_FAIL_PCT:
            failed = {
                "reason": "daily_loss",
                "when": when,
                "challenge": challenge_num,
                "day_pct": round(day_pct, 3),
                "dd_pct": round(dd_pct, 3),
                "from_start_pct": round(from_start, 3),
                "equity": round(equity, 2),
                "passes_before_fail": len(passes),
            }
            events.append({"type": "FAIL", **failed})
            break

        if dd_pct >= DD_FAIL_PCT:
            failed = {
                "reason": f"drawdown_{dd_mode}",
                "when": when,
                "challenge": challenge_num,
                "day_pct": round(day_pct, 3),
                "dd_pct": round(dd_pct, 3),
                "from_start_pct": round(from_start, 3),
                "equity": round(equity, 2),
                "passes_before_fail": len(passes),
            }
            events.append({"type": "FAIL", **failed})
            break

        if from_start >= PASS_PCT:
            rec = {
                "challenge": challenge_num,
                "when": when,
                "equity": round(equity, 2),
                "from_start_pct": round(from_start, 3),
                "days_in_challenge": None,
            }
            if passes:
                # rough: use timestamps
                pass
            passes.append(rec)
            events.append({"type": "PASS", **rec})
            # New evaluation starts at current equity (same trading continuum)
            challenge_num += 1
            challenge_start = equity
            challenge_peak = equity

    return {
        "dd_mode": dd_mode,
        "passes": len(passes),
        "pass_list": passes,
        "failed": failed,
        "events": events,
        "final_equity": round(equity, 2),
        "challenges_started": challenge_num if not failed else challenge_num,
        "fees_if_each_attempt": round(FEE_USD * (len(passes) + (1 if failed or True else 0)), 2),
    }


def main():
    t0 = time.time()
    b5, b15, b1h = load_365()
    ov = locked_ov()
    print("Collecting short-only trades (no internal risk halt)…", flush=True)
    trades = collect_trades(b5, b15, b1h, ov)
    print(f"Closed trades: {len(trades)}", flush=True)

    results = {}
    for mode in ("static", "trailing"):
        print(f"\n--- Challenge sim dd_mode={mode} ---", flush=True)
        r = simulate_challenges(trades, dd_mode=mode)
        results[mode] = r
        print(f"  PASSES: {r['passes']}", flush=True)
        for p in r["pass_list"]:
            print(
                f"    ✓ challenge #{p['challenge']} @ {p['when']} "
                f"eq=${p['equity']:,.0f} (+{p['from_start_pct']:.1f}%)",
                flush=True,
            )
        if r["failed"]:
            f = r["failed"]
            print(
                f"  FAIL: {f['reason']} @ {f['when']} "
                f"(challenge #{f['challenge']}, after {f['passes_before_fail']} passes) "
                f"day={f['day_pct']}% dd={f['dd_pct']}%",
                flush=True,
            )
        else:
            print("  No rule breach before end of year.", flush=True)
        # Fee model: pay $800 per attempt started
        attempts = r["passes"] + (1 if r["failed"] else (1 if r["passes"] == 0 else 0))
        # If ended year mid-challenge without fail: still paid for that open attempt
        if not r["failed"]:
            attempts = r["passes"] + 1  # last unfinished challenge paid
        print(
            f"  Attempts paid (~${FEE_USD:.0f} each): {attempts} → "
            f"${attempts * FEE_USD:,.0f} fees | final equity ${r['final_equity']:,.0f}",
            flush=True,
        )

    out = {
        "plan": {
            "name": "Starter",
            "account": ACCOUNT,
            "profit_target_pct": PASS_PCT,
            "daily_loss_pct": DAILY_FAIL_PCT,
            "max_dd_pct": DD_FAIL_PCT,
            "fee_usd": FEE_USD,
        },
        "strategy": "short ASIA+LNDN engulf1.5 tp1.2R risk1.35%",
        "trades": len(trades),
        "period": {
            "start": datetime.fromtimestamp(b5[0]["timestamp"], tz=timezone.utc).isoformat(),
            "end": datetime.fromtimestamp(b5[-1]["timestamp"], tz=timezone.utc).isoformat(),
        },
        "elapsed_sec": round(time.time() - t0, 1),
        "results": results,
    }
    path = ROOT / "data" / "prop_challenge_1y.json"
    path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nSaved {path}", flush=True)


if __name__ == "__main__":
    main()
