#!/usr/bin/env python3
"""
Tune hermes-smc toward:
  - winrate > 60%
  - ~10% account gain / month
  - max daily loss ≤ 2%
  - max drawdown ≤ 6%

Enforces a daily loss halt in the sim (no new entries after -2% day).
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

TARGET_WR = 60.0
TARGET_MO = 10.0
MAX_DAILY_LOSS_PCT = 2.0
MAX_DD_PCT = 6.0


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_cached_bars():
    files = sorted(CACHE_DIR.glob("okx_BTCUSDT_*_5m.json"), key=lambda p: p.stat().st_size)
    if not files:
        raise SystemExit("No OKX cache — run scripts/backtest_3m.py first")
    f5 = files[-1]
    stem = f5.name.replace("_5m.json", "")
    raw5 = json.loads(f5.read_text())
    raw15 = json.loads((CACHE_DIR / f"{stem}_15m.json").read_text())
    raw1h = json.loads((CACHE_DIR / f"{stem}_1h.json").read_text())
    print(f"Cache {f5.name}: {len(raw5['candles'])} bars", flush=True)
    return raw5["candles"], raw15["candles"], raw1h["candles"]


def session_windows(enabled: set[str]) -> list[dict]:
    return [
        {"name": "ASIA", "start": "20:00", "end": "02:00", "enabled": "ASIA" in enabled},
        {"name": "LNDN", "start": "02:00", "end": "09:30", "enabled": "LNDN" in enabled},
        {"name": "NYAM", "start": "09:30", "end": "13:30", "enabled": "NYAM" in enabled},
        {"name": "NYPM", "start": "13:30", "end": "20:00", "enabled": "NYPM" in enabled},
    ]


def day_key(ts: float) -> str:
    # Use America/New_York trading day for daily loss
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(ts, tz=ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def run_with_overrides(bars_5m, bars_15m, bars_1h, overrides: dict) -> dict:
    cfg = SMCConfig(CONFIG_PATH)
    cfg._config = deep_merge(cfg._config, overrides)
    sessions = dict(cfg.get("sessions", {}) or {})
    sessions["filter_entries"] = False
    cfg._config["sessions"] = sessions

    daily_halt_pct = float(overrides.get("_max_daily_loss_pct", MAX_DAILY_LOSS_PCT))

    engine = PaperTradingEngine(cfg)
    pm = engine.position_manager
    pm.state_dir = None
    pm.open_positions = {}
    pm.closed_positions = []
    pm.trade_history = []
    initial = float(cfg.get("paper_trading.initial_capital", 100_000))
    pm.capital = initial
    pm.initial_capital = initial

    session_cfg = cfg.get("sessions") or {}
    enabled_names = {w["name"] for w in session_cfg.get("windows", []) if w.get("enabled", True)}
    allowed_sides = set(overrides.get("_allowed_sides") or ["long", "short"])
    min_body = float(overrides.get("_min_engulf_body_pct") or 0.0)
    cooldown = float(cfg.get("entry.cooldown_seconds", 300))
    max_open = int(cfg.get("entry.max_open_positions", 1))
    last_trade_ts = 0.0
    win_5m = 500

    equity = initial
    peak = initial
    max_dd_pct = 0.0
    day_start_equity: dict[str, float] = {}
    day_pnl: dict[str, float] = {}
    halted_days: set[str] = set()
    equity_curve = []

    def note_close(pnl: float, ts: float):
        nonlocal equity, peak, max_dd_pct
        equity += pnl
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100 if peak > 0 else 0.0
        max_dd_pct = max(max_dd_pct, dd)
        dk = day_key(ts)
        if dk not in day_start_equity:
            day_start_equity[dk] = equity - pnl  # equity before this close
        day_pnl[dk] = day_pnl.get(dk, 0.0) + pnl
        # Halt new entries if day loss vs day-start equity exceeds limit
        start_eq = day_start_equity[dk]
        day_pct = (day_pnl[dk] / start_eq * 100) if start_eq else 0.0
        if day_pct <= -daily_halt_pct:
            halted_days.add(dk)
        equity_curve.append({"ts": ts, "equity": equity, "pnl": pnl, "dd_pct": dd})

    for i in range(max(win_5m, 120), len(bars_5m)):
        bar = bars_5m[i]
        ts = bar["timestamp"]
        c5 = bars_5m[max(0, i + 1 - win_5m): i + 1]
        c15 = align_upto(bars_15m, ts)[-200:]
        c1h = align_upto(bars_1h, ts)[-200:]
        if len(c15) < 60 or len(c1h) < 60:
            continue

        for trade_id, position in list(pm.open_positions.items()):
            reason = try_exit_on_bar(engine, position, bar)
            if reason:
                fill = exit_fill_price(position, reason, bar)
                exit_rsi = engine._calc_rsi(c5, int(cfg.get("rsi.period", 14)))
                closed = pm.close_position(trade_id, fill, reason, rsi_at_exit=exit_rsi)
                if closed:
                    closed["exit_time"] = float(ts)  # bar time, not wall clock
                    note_close(float(closed.get("pnl") or 0), float(ts))
                last_trade_ts = ts

        dk = day_key(ts)
        if dk in halted_days:
            continue
        if len(pm.open_positions) >= max_open or ts - last_trade_ts < cooldown:
            continue

        locked_ts = c5[-2]["timestamp"]
        sess = session_from_ts(locked_ts, session_cfg)
        if sess not in enabled_names:
            continue

        signal = engine.detect_entry_signal(c5, c15, c1h, candles_1m=None)
        if not signal or signal["position_size"] <= 0:
            continue
        side = signal.get("side", "long")
        if side not in allowed_sides:
            continue
        if min_body > 0:
            eng = c5[-2]
            if abs(eng["close"] - eng["open"]) / eng["close"] < min_body:
                continue

        rsi = signal.get("rsi")
        trade_id = str(uuid.uuid4())
        pm.open_position(
            trade_id=trade_id,
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
                "rsi_at_entry": round(float(rsi), 2) if rsi is not None else None,
            },
        )
        pm.open_positions[trade_id]["open_time"] = float(locked_ts)
        last_trade_ts = ts

    if pm.open_positions:
        last = bars_5m[-1]
        for trade_id in list(pm.open_positions):
            exit_rsi = engine._calc_rsi(bars_5m[-win_5m:], int(cfg.get("rsi.period", 14)))
            closed = pm.close_position(
                trade_id, float(last["close"]), "end_of_backtest", rsi_at_exit=exit_rsi
            )
            if closed:
                closed["exit_time"] = float(last["timestamp"])
                note_close(float(closed.get("pnl") or 0), float(last["timestamp"]))

    enriched = [enrich_trade_meta(t, initial, session_cfg) for t in pm.closed_positions]
    wins = [t for t in enriched if float(t.get("pnl") or 0) > 0]
    losses = [t for t in enriched if float(t.get("pnl") or 0) <= 0]
    total_pnl = equity - initial
    per_mo = (total_pnl / initial * 100 / 3.0) if initial else 0.0
    wr = (100.0 * len(wins) / len(enriched)) if enriched else 0.0

    # Worst daily loss % vs that day's starting equity
    worst_day_pct = 0.0
    for dk, pnl in day_pnl.items():
        start_eq = day_start_equity.get(dk, initial)
        pct = (pnl / start_eq * 100) if start_eq else 0.0
        worst_day_pct = min(worst_day_pct, pct)

    # Monthly buckets
    months = {}
    for t in enriched:
        ts = t.get("exit_time") or t.get("open_time")
        if not ts:
            continue
        key = datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m")
        months[key] = months.get(key, 0.0) + float(t.get("pnl") or 0)
    month_rows = [
        {"month": k, "pnl": round(v, 2), "pct": round(v / initial * 100, 3)}
        for k, v in sorted(months.items())
    ]

    return {
        "trades": len(enriched),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(wr, 2),
        "total_pnl": round(total_pnl, 2),
        "total_account_pct": round(total_pnl / initial * 100, 3) if initial else 0.0,
        "per_month_simple_pct": round(per_mo, 3),
        "max_dd_pct": round(max_dd_pct, 3),
        "worst_day_pct": round(worst_day_pct, 3),
        "halted_days": len(halted_days),
        "months": month_rows,
        "profit_factor": (
            round(
                abs(sum(float(t["pnl"]) for t in wins))
                / max(1e-9, abs(sum(float(t["pnl"]) for t in losses))),
                3,
            )
            if wins and losses
            else None
        ),
        "overrides": overrides,
        "passes": (
            wr >= TARGET_WR
            and per_mo >= TARGET_MO
            and max_dd_pct <= MAX_DD_PCT
            and worst_day_pct >= -MAX_DAILY_LOSS_PCT - 0.05  # tiny float slack
            and len(enriched) >= 15
        ),
    }


def build_ov(sessions, sides, mode, tp_rr, be_at, trail_rr, long_max, short_min, risk, cd, body=0.0):
    return {
        "exits": {
            "mode": mode,
            "be_at_rr": be_at,
            "trail_after_be": mode == "be_trail",
            "trail_rr": trail_rr,
            "tp_rr": tp_rr,
            "structure_break": False,
        },
        "risk": {"risk_pct_per_trade": risk, "rr_target": tp_rr},
        "rsi": {"enabled": True, "period": 14, "long_max": long_max, "short_min": short_min},
        "entry": {"cooldown_seconds": cd, "engulf_lookback": 2, "max_open_positions": 1},
        "sessions": {
            "timezone": "America/New_York",
            "filter_entries": True,
            "windows": session_windows(set(sessions)),
        },
        "_allowed_sides": sides,
        "_min_engulf_body_pct": body,
        "_max_daily_loss_pct": MAX_DAILY_LOSS_PCT,
    }


def meets_risk_caps(r: dict) -> bool:
    return r["max_dd_pct"] <= MAX_DD_PCT and r["worst_day_pct"] >= -MAX_DAILY_LOSS_PCT - 0.05


def main():
    bars_5m, bars_15m, bars_1h = load_cached_bars()
    t0 = time.time()
    results = []
    hits = []

    # Risk must stay modest under 2% daily / 6% MDD constraints.
    base_cfgs = []
    for sessions in (["LNDN"], ["LNDN", "ASIA"], ["LNDN", "NYPM"]):
        for sides in (["short"], ["long", "short"]):
            for tp in (0.8, 1.0, 1.2, 1.5):
                for mode in ("fixed_tp", "be_trail"):
                    for long_max, short_min in ((55, 45), (60, 40), (50, 50)):
                        be = 0.55 if tp <= 1.0 else 0.75
                        trail = 0.35 if mode == "be_trail" else 1.0
                        base_cfgs.append(
                            build_ov(sessions, sides, mode, tp, be, trail, long_max, short_min, 0.5, 300)
                        )

    print(f"Stage A: {len(base_cfgs)} skeletons @ 0.5% risk", flush=True)
    seeds = []
    for i, ov in enumerate(base_cfgs):
        r = run_with_overrides(bars_5m, bars_15m, bars_1h, ov)
        results.append(r)
        if r["win_rate_pct"] >= TARGET_WR and r["trades"] >= 15 and meets_risk_caps(r):
            seeds.append(r)
            print(
                f"  SEED wr={r['win_rate_pct']}% n={r['trades']} mo={r['per_month_simple_pct']}% "
                f"dd={r['max_dd_pct']}% day={r['worst_day_pct']}% "
                f"sides={ov['_allowed_sides']} tp={ov['risk']['rr_target']} "
                f"sess={[w['name'] for w in ov['sessions']['windows'] if w['enabled']]}",
                flush=True,
            )
        if i % 20 == 0:
            print(f"  … {i}/{len(base_cfgs)} seeds={len(seeds)} [{time.time()-t0:.0f}s]", flush=True)

    if not seeds:
        seeds = sorted(
            [r for r in results if r["trades"] >= 12],
            key=lambda r: (r["win_rate_pct"], r["per_month_simple_pct"]),
            reverse=True,
        )[:10]
        print(f"No strict seeds — using top {len(seeds)} by WR", flush=True)
    else:
        seeds = sorted(
            seeds,
            key=lambda r: (r["per_month_simple_pct"], r["win_rate_pct"], r["trades"]),
            reverse=True,
        )[:20]

    print(f"Stage B: scale risk on {len(seeds)} seeds (cap risk so DD/daily hold)", flush=True)
    for seed in seeds:
        base = seed["overrides"]
        # Theoretical: N consecutive losses * risk ≈ DD. Keep risk ≤ MDD/4 ≈ 1.5
        # Daily: risk ≤ 2% (halt helps if multiple)
        for risk in (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0):
            for body in (0.0, 0.0005):
                ov = copy.deepcopy(base)
                ov["risk"]["risk_pct_per_trade"] = risk
                ov["_min_engulf_body_pct"] = body
                r = run_with_overrides(bars_5m, bars_15m, bars_1h, ov)
                results.append(r)
                tag = (
                    f"wr={r['win_rate_pct']}% mo={r['per_month_simple_pct']}% "
                    f"dd={r['max_dd_pct']}% day={r['worst_day_pct']}% n={r['trades']} risk={risk}"
                )
                if r["passes"]:
                    hits.append(r)
                    print(f"  HIT {tag}", flush=True)
                elif r["win_rate_pct"] >= TARGET_WR and r["per_month_simple_pct"] >= 8 and meets_risk_caps(r):
                    print(f"  near {tag}", flush=True)

    # Stage C: if no hit, try more selective entries (stricter RSI / body) + risk 1.5-2
    if not hits:
        print("Stage C: stricter filters for higher WR + denser quality trades", flush=True)
        extra = []
        for sessions in (["LNDN"], ["LNDN", "ASIA"]):
            for sides in (["short"], ["long", "short"]):
                for tp in (0.8, 1.0, 1.2):
                    for long_max, short_min in ((52, 48), (55, 45), (48, 52)):
                        for risk in (1.0, 1.5, 2.0):
                            for body in (0.0, 0.0006, 0.001):
                                for mode in ("fixed_tp", "be_trail"):
                                    extra.append(
                                        build_ov(
                                            sessions, sides, mode, tp,
                                            0.5 if tp <= 1 else 0.7,
                                            0.35, long_max, short_min, risk, 300, body,
                                        )
                                    )
        print(f"  extra {len(extra)}", flush=True)
        for i, ov in enumerate(extra):
            r = run_with_overrides(bars_5m, bars_15m, bars_1h, ov)
            results.append(r)
            if r["passes"]:
                hits.append(r)
                print(
                    f"  HIT wr={r['win_rate_pct']}% mo={r['per_month_simple_pct']}% "
                    f"dd={r['max_dd_pct']}% day={r['worst_day_pct']}% n={r['trades']}",
                    flush=True,
                )
            if i % 25 == 0:
                print(f"  … {i}/{len(extra)} hits={len(hits)} [{time.time()-t0:.0f}s]", flush=True)

    def rank(r):
        return (
            1 if r.get("passes") else 0,
            r["per_month_simple_pct"] if meets_risk_caps(r) else -999,
            r["win_rate_pct"],
            -(r["max_dd_pct"]),
            r["trades"],
        )

    hits = sorted(hits, key=rank, reverse=True)
    top = hits[:5] if hits else sorted(results, key=rank, reverse=True)[:8]
    out = {
        "targets": {
            "win_rate_pct": TARGET_WR,
            "per_month_pct": TARGET_MO,
            "max_daily_loss_pct": MAX_DAILY_LOSS_PCT,
            "max_dd_pct": MAX_DD_PCT,
        },
        "hits": len(hits),
        "elapsed_sec": round(time.time() - t0, 1),
        "top": [
            {k: r[k] for k in (
                "win_rate_pct", "per_month_simple_pct", "total_account_pct",
                "trades", "profit_factor", "max_dd_pct", "worst_day_pct",
                "halted_days", "months", "passes", "overrides",
            )}
            for r in top
        ],
    }
    path = ROOT / "data" / "tune_results.json"
    path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nSaved {path} hits={len(hits)} [{time.time()-t0:.0f}s]", flush=True)
    print(json.dumps(out["top"][0], indent=2, default=str)[:2500], flush=True)


if __name__ == "__main__":
    main()
