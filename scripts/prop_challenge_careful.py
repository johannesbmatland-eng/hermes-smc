#!/usr/bin/env python3
"""
Prop Starter challenge sim with protective risk gates (like live bot).
Halt new entries before daily 3% / static DD 6% would be breached.
"""

from __future__ import annotations

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
from hermes_smc.engine.analytics import session_from_ts  # noqa: E402
from hermes_smc.engine.paper_trading import PaperTradingEngine  # noqa: E402
from hermes_smc.engine.smc_engine import SMCConfig  # noqa: E402
from scripts.tune_6m import build_ov, deep_merge, day_key  # noqa: E402

ACCOUNT = 100_000.0
PASS_PCT = 10.0
DAILY_FAIL = 3.0
DD_FAIL = 6.0
FEE = 800.0
RISK_PCT = 1.35


def load_365():
    f5 = sorted(CACHE_DIR.glob("okx_BTCUSDT_365d_*_5m.json"), key=lambda p: p.stat().st_size)[-1]
    stem = f5.name.replace("_5m.json", "")
    return (
        json.loads(f5.read_text())["candles"],
        json.loads((CACHE_DIR / f"{stem}_15m.json").read_text())["candles"],
        json.loads((CACHE_DIR / f"{stem}_1h.json").read_text())["candles"],
        f5.name,
    )


def ov():
    o = build_ov(
        sessions=["ASIA", "LNDN"],
        sides=["short"],
        mode="fixed_tp",
        tp=1.2,
        be=0.55,
        trail=1.0,
        long_max=55,
        short_min=45,
        risk=RISK_PCT,
        cd=120,
        lookback=2,
        sl_buffer=0.0003,
        trend_method="ema_majority",
        engulf_ratio=1.5,
    )
    o["risk"]["max_daily_loss_pct"] = 99
    o["risk"]["max_drawdown_pct"] = 99
    o["_max_daily_loss_pct"] = 99
    return o


def run(dd_mode: str = "static"):
    bars_5m, bars_15m, bars_1h, tag = load_365()
    cfg = SMCConfig(CONFIG_PATH)
    cfg._config = deep_merge(cfg._config, ov())
    sessions = dict(cfg.get("sessions", {}) or {})
    sessions["filter_entries"] = False
    cfg._config["sessions"] = sessions

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
    cooldown = float(cfg.get("entry.cooldown_seconds", 120))
    last_trade_ts = 0.0
    win_5m = 500

    equity = ACCOUNT
    challenge_start = ACCOUNT
    challenge_peak = ACCOUNT
    challenge_num = 1
    passes = []
    failed = None
    halted_entries = 0
    cur_day = None
    day_start = ACCOUNT
    day_pnl = 0.0

    def maybe_pass_fail(ts, pnl_just):
        nonlocal equity, challenge_start, challenge_peak, challenge_num, failed, day_pnl
        equity = pm.capital  # realized
        challenge_peak = max(challenge_peak, equity)
        day_pnl += pnl_just
        day_pct = day_pnl / day_start * 100 if day_start else 0.0
        if dd_mode == "trailing":
            dd_pct = (challenge_peak - equity) / challenge_peak * 100 if challenge_peak else 0.0
        else:
            dd_pct = (challenge_start - equity) / challenge_start * 100 if challenge_start else 0.0
        from_start = (equity - challenge_start) / challenge_start * 100 if challenge_start else 0.0
        when = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

        if day_pct <= -DAILY_FAIL + 1e-9:
            failed = {
                "reason": "daily_loss",
                "when": when,
                "challenge": challenge_num,
                "day_pct": round(day_pct, 3),
                "dd_pct": round(dd_pct, 3),
                "passes_before": len(passes),
                "equity": round(equity, 2),
            }
            return
        if dd_pct >= DD_FAIL - 1e-9:
            failed = {
                "reason": f"drawdown_{dd_mode}",
                "when": when,
                "challenge": challenge_num,
                "day_pct": round(day_pct, 3),
                "dd_pct": round(dd_pct, 3),
                "passes_before": len(passes),
                "equity": round(equity, 2),
            }
            return
        if from_start >= PASS_PCT - 1e-9:
            passes.append(
                {
                    "challenge": challenge_num,
                    "when": when,
                    "equity": round(equity, 2),
                    "from_start_pct": round(from_start, 3),
                }
            )
            challenge_num += 1
            challenge_start = equity
            challenge_peak = equity

    def can_enter(ts):
        nonlocal halted_entries
        # room for another full loss?
        day_pct = day_pnl / day_start * 100 if day_start else 0.0
        if day_pct - RISK_PCT < -DAILY_FAIL:
            halted_entries += 1
            return False
        if dd_mode == "trailing":
            peak = max(challenge_peak, equity)
            dd_now = (peak - equity) / peak * 100 if peak else 0.0
        else:
            dd_now = (challenge_start - equity) / challenge_start * 100 if challenge_start else 0.0
        # another full risk loss as % of challenge_start (static) approx
        risk_dollars = equity * (RISK_PCT / 100)
        if dd_mode == "static":
            after = equity - risk_dollars
            dd_after = (challenge_start - after) / challenge_start * 100
            if dd_after >= DD_FAIL:
                halted_entries += 1
                return False
        else:
            peak = max(challenge_peak, equity)
            after = equity - risk_dollars
            dd_after = (peak - after) / peak * 100
            if dd_after >= DD_FAIL:
                halted_entries += 1
                return False
        return True

    for i in range(max(win_5m, 120), len(bars_5m)):
        if failed:
            break
        bar = bars_5m[i]
        ts = bar["timestamp"]
        c5 = bars_5m[max(0, i + 1 - win_5m) : i + 1]
        c15 = align_upto(bars_15m, ts)[-200:]
        c1h = align_upto(bars_1h, ts)[-200:]
        if len(c15) < 60 or len(c1h) < 60:
            continue

        dk = day_key(ts)
        if cur_day != dk:
            cur_day = dk
            day_start = equity
            day_pnl = 0.0

        for tid, pos in list(pm.open_positions.items()):
            reason = try_exit_on_bar(engine, pos, bar)
            if reason:
                fill = exit_fill_price(pos, reason, bar)
                exit_rsi = engine._calc_rsi(c5, int(cfg.get("rsi.period", 14)))
                closed = pm.close_position(tid, fill, reason, rsi_at_exit=exit_rsi)
                if closed:
                    closed["exit_time"] = float(ts)
                    maybe_pass_fail(float(ts), float(closed.get("pnl") or 0))
                last_trade_ts = ts
                if failed:
                    break

        if failed:
            break
        if len(pm.open_positions) >= 1 or ts - last_trade_ts < cooldown:
            continue
        if not can_enter(ts):
            continue

        locked_ts = c5[-2]["timestamp"]
        sess = session_from_ts(locked_ts, session_cfg)
        if sess not in enabled:
            continue
        signal = engine.detect_entry_signal(c5, c15, c1h, candles_1m=None)
        if not signal or signal["position_size"] <= 0:
            continue
        if signal.get("side") != "short":
            continue

        tid = str(uuid.uuid4())
        pm.open_position(
            trade_id=tid,
            asset="BTC/USD",
            side="short",
            entry_price=signal["entry_price"],
            position_size=signal["position_size"],
            sl_price=signal["sl_price"],
            tp_price=signal["tp_price"],
            strategy_info={"session": sess, "side": "short"},
        )
        pm.open_positions[tid]["open_time"] = float(locked_ts)
        last_trade_ts = ts

    # end open
    if not failed and pm.open_positions:
        last = bars_5m[-1]
        for tid in list(pm.open_positions):
            closed = pm.close_position(tid, float(last["close"]), "end_of_backtest")
            if closed:
                maybe_pass_fail(float(last["timestamp"]), float(closed.get("pnl") or 0))

    attempts = len(passes) + 1  # current / failed attempt
    return {
        "tag": tag,
        "dd_mode": dd_mode,
        "passes": len(passes),
        "pass_list": passes,
        "failed": failed,
        "final_equity": round(pm.capital, 2),
        "halted_entry_skips": halted_entries,
        "closed_trades": len(pm.closed_positions),
        "fee_total_usd": round(attempts * FEE, 2),
        "attempts": attempts,
    }


def main():
    t0 = time.time()
    out = {"plan": {
        "profit_target_pct": PASS_PCT,
        "daily_loss_pct": DAILY_FAIL,
        "max_dd_pct": DD_FAIL,
        "account": ACCOUNT,
        "fee": FEE,
        "note": "Protective gates: skip new entries that could breach daily/DD",
    }, "results": {}}
    for mode in ("static", "trailing"):
        print(f"\n=== Careful sim dd={mode} ===", flush=True)
        r = run(mode)
        out["results"][mode] = r
        print(f"PASSES: {r['passes']}  trades={r['closed_trades']}  "
              f"halt_skips={r['halted_entry_skips']}  final=${r['final_equity']:,.0f}", flush=True)
        for p in r["pass_list"]:
            print(f"  ✓ #{p['challenge']} {p['when']} ${p['equity']:,.0f} (+{p['from_start_pct']:.1f}%)", flush=True)
        if r["failed"]:
            f = r["failed"]
            print(f"  ✗ FAIL {f['reason']} @ {f['when']} after {f['passes_before']} passes "
                  f"day={f['day_pct']}% dd={f['dd_pct']}%", flush=True)
        else:
            print("  No hard fail — year ended mid-challenge or after passes.", flush=True)
        print(f"  Fees ~${r['fee_total_usd']:,.0f} ({r['attempts']} × ${FEE:.0f})", flush=True)

    out["elapsed_sec"] = round(time.time() - t0, 1)
    path = ROOT / "data" / "prop_challenge_1y_careful.json"
    path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nSaved {path}", flush=True)


if __name__ == "__main__":
    main()
