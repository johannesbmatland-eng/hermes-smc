#!/usr/bin/env python3
"""
6-month tune: WR>60%, ≥10%/mo, daily loss≤2%, MDD≤6%.
"""

from __future__ import annotations

import copy
import json
import sys
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
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

TARGET_WR = 60.0
TARGET_MO = 10.0
MAX_DAILY_LOSS_PCT = 2.0
MAX_DD_PCT = 6.0
PERIOD_MONTHS = 6.0


def deep_merge(base, override):
    out = copy.deepcopy(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_cached_bars():
    files = sorted(CACHE_DIR.glob("okx_BTCUSDT_180d_*_5m.json"), key=lambda p: p.stat().st_size)
    if not files:
        files = sorted(CACHE_DIR.glob("okx_BTCUSDT_*_5m.json"), key=lambda p: p.stat().st_size)
    if not files:
        raise SystemExit("No cache")
    f5 = files[-1]
    stem = f5.name.replace("_5m.json", "")
    raw5 = json.loads(f5.read_text())
    raw15 = json.loads((CACHE_DIR / f"{stem}_15m.json").read_text())
    raw1h = json.loads((CACHE_DIR / f"{stem}_1h.json").read_text())
    months = 6.0 if "180d" in f5.name else 3.0
    print(f"Cache {f5.name}: {len(raw5['candles'])} bars (~{months:.0f}m)", flush=True)
    return raw5["candles"], raw15["candles"], raw1h["candles"], months


def session_windows(enabled):
    return [
        {"name": "ASIA", "start": "20:00", "end": "02:00", "enabled": "ASIA" in enabled},
        {"name": "LNDN", "start": "02:00", "end": "09:30", "enabled": "LNDN" in enabled},
        {"name": "NYAM", "start": "09:30", "end": "13:30", "enabled": "NYAM" in enabled},
        {"name": "NYPM", "start": "13:30", "end": "20:00", "enabled": "NYPM" in enabled},
    ]


def day_key(ts):
    return datetime.fromtimestamp(ts, tz=ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def build_ov(sessions, sides, mode, tp, be, trail, long_max, short_min, risk, cd,
             body=0.0, lookback=2, sl_buffer=0.0003, rsi_band=None, min_fvg_age=50,
             trend_method="ema_majority", engulf_ratio=1.0, engulf_body_pct=0.0):
    ov = {
        "exits": {
            "mode": mode,
            "be_at_rr": be,
            "trail_after_be": mode == "be_trail",
            "trail_rr": trail,
            "tp_rr": tp,
            "structure_break": False,
        },
        "risk": {
            "risk_pct_per_trade": risk,
            "rr_target": tp,
            "sl_buffer_pct": sl_buffer,
            "max_daily_loss_pct": MAX_DAILY_LOSS_PCT,
            "max_drawdown_pct": MAX_DD_PCT,
        },
        "rsi": {
            "enabled": True,
            "period": 14,
            "long_max": long_max,
            "short_min": short_min,
        },
        "entry": {
            "cooldown_seconds": cd,
            "engulf_lookback": lookback,
            "max_open_positions": 1,
            "allowed_sides": sides,
            "min_engulf_body_ratio": engulf_ratio,
            "min_engulf_body_pct": engulf_body_pct,
        },
        "trend_filter": {
            "enabled": True,
            "method": trend_method,
            "ema_period": 50,
        },
        "fvq_detection": {
            "min_candles_since_fvg": min_fvg_age,
            "fvg_buffer_pct": 0.001,
        },
        "sessions": {
            "timezone": "America/New_York",
            "filter_entries": True,
            "windows": session_windows(set(sessions)),
        },
        "_allowed_sides": sides,
        "_min_engulf_body_pct": body,
        "_max_daily_loss_pct": MAX_DAILY_LOSS_PCT,
    }
    if rsi_band:
        ov["rsi"]["entry_band"] = list(rsi_band)
    return ov


def run_bt(bars_5m, bars_15m, bars_1h, overrides, period_months=6.0):
    cfg = SMCConfig(CONFIG_PATH)
    cfg._config = deep_merge(cfg._config, overrides)
    sessions = dict(cfg.get("sessions", {}) or {})
    sessions["filter_entries"] = False
    cfg._config["sessions"] = sessions

    daily_halt = float(overrides.get("_max_daily_loss_pct", MAX_DAILY_LOSS_PCT))
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
    enabled = {w["name"] for w in session_cfg.get("windows", []) if w.get("enabled", True)}
    allowed = set(overrides.get("_allowed_sides") or cfg.get("entry.allowed_sides") or ["long", "short"])
    min_body = float(overrides.get("_min_engulf_body_pct") or 0.0)
    cooldown = float(cfg.get("entry.cooldown_seconds", 300))
    max_open = int(cfg.get("entry.max_open_positions", 1))
    rsi_band = cfg.get("rsi.entry_band")
    last_trade_ts = 0.0
    win_5m = 500

    equity = initial
    peak = initial
    max_dd = 0.0
    day_start = {}
    day_pnl = {}
    halted = set()

    def on_close(pnl, ts):
        nonlocal equity, peak, max_dd
        equity += pnl
        peak = max(peak, equity)
        dd = (peak - equity) / peak * 100 if peak else 0.0
        max_dd = max(max_dd, dd)
        dk = day_key(ts)
        if dk not in day_start:
            day_start[dk] = equity - pnl
        day_pnl[dk] = day_pnl.get(dk, 0.0) + pnl
        start = day_start[dk]
        if start and (day_pnl[dk] / start * 100) <= -daily_halt:
            halted.add(dk)

    for i in range(max(win_5m, 120), len(bars_5m)):
        bar = bars_5m[i]
        ts = bar["timestamp"]
        c5 = bars_5m[max(0, i + 1 - win_5m): i + 1]
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
                    on_close(float(closed.get("pnl") or 0), float(ts))
                last_trade_ts = ts

        if day_key(ts) in halted:
            continue
        if len(pm.open_positions) >= max_open or ts - last_trade_ts < cooldown:
            continue

        # Pre-trade daily budget: another full risk loss must not breach daily cap
        risk_pct = float(cfg.get("risk.risk_pct_per_trade", 0.5))
        dk = day_key(ts)
        if dk in day_start:
            day_pct_now = day_pnl.get(dk, 0.0) / day_start[dk] * 100 if day_start[dk] else 0.0
            if day_pct_now - risk_pct < -daily_halt - 1e-9:
                halted.add(dk)
                continue
        else:
            # First trade of day: risk itself must be ≤ daily halt
            if risk_pct > daily_halt + 1e-9:
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
        rsi = signal.get("rsi")
        if rsi is not None and isinstance(rsi_band, (list, tuple)) and len(rsi_band) == 2:
            if not (float(rsi_band[0]) <= float(rsi) <= float(rsi_band[1])):
                continue
        if min_body > 0:
            eng = c5[-2]
            if abs(eng["close"] - eng["open"]) / eng["close"] < min_body:
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
                "rsi_at_entry": round(float(rsi), 2) if rsi is not None else None,
            },
        )
        pm.open_positions[tid]["open_time"] = float(locked_ts)
        last_trade_ts = ts

    if pm.open_positions:
        last = bars_5m[-1]
        for tid in list(pm.open_positions):
            exit_rsi = engine._calc_rsi(bars_5m[-win_5m:], int(cfg.get("rsi.period", 14)))
            closed = pm.close_position(tid, float(last["close"]), "end_of_backtest", rsi_at_exit=exit_rsi)
            if closed:
                closed["exit_time"] = float(last["timestamp"])
                on_close(float(closed.get("pnl") or 0), float(last["timestamp"]))

    enriched = [enrich_trade_meta(t, initial, session_cfg) for t in pm.closed_positions]
    wins = [t for t in enriched if float(t.get("pnl") or 0) > 0]
    losses = [t for t in enriched if float(t.get("pnl") or 0) <= 0]
    total_pnl = equity - initial
    total_pct = total_pnl / initial * 100 if initial else 0.0
    per_mo = total_pct / period_months
    wr = 100.0 * len(wins) / len(enriched) if enriched else 0.0

    worst_day = 0.0
    for dk, pnl in day_pnl.items():
        start = day_start.get(dk, initial)
        pct = pnl / start * 100 if start else 0.0
        worst_day = min(worst_day, pct)

    months: dict[str, dict] = {}
    for t in enriched:
        ts = t.get("exit_time") or t.get("open_time")
        if not ts:
            continue
        key = datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m")
        row = months.setdefault(key, {"pnl": 0.0, "trades": 0, "wins": 0})
        pnl = float(t.get("pnl") or 0)
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
                "pnl": round(v["pnl"], 2),
                "pct": round(v["pnl"] / initial * 100, 3),
                "trades": n,
                "wins": v["wins"],
                "win_rate_pct": round(100.0 * v["wins"] / n, 1) if n else 0.0,
            }
        )

    risk_ok = max_dd <= MAX_DD_PCT + 1e-6 and worst_day >= -(MAX_DAILY_LOSS_PCT + 0.05)
    passes = (
        wr >= TARGET_WR
        and per_mo >= TARGET_MO
        and risk_ok
        and len(enriched) >= 25
    )
    return {
        "trades": len(enriched),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(wr, 2),
        "total_pnl": round(total_pnl, 2),
        "total_account_pct": round(total_pct, 3),
        "per_month_simple_pct": round(per_mo, 3),
        "max_dd_pct": round(max_dd, 3),
        "worst_day_pct": round(worst_day, 3),
        "halted_days": len(halted),
        "months": month_rows,
        "profit_factor": (
            round(
                abs(sum(float(t["pnl"]) for t in wins))
                / max(1e-9, abs(sum(float(t["pnl"]) for t in losses))),
                3,
            )
            if wins and losses else None
        ),
        "passes": passes,
        "risk_ok": risk_ok,
        "overrides": overrides,
        "period_months": period_months,
    }


# Workers reload cache from disk (avoid pickling 50k bars)
_CACHE_STEM = None
_MONTHS = 6.0
_B5 = _B15 = _B1H = None


def _init_worker(stem: str, months: float):
    global _CACHE_STEM, _MONTHS, _B5, _B15, _B1H
    _CACHE_STEM = stem
    _MONTHS = months
    raw5 = json.loads((CACHE_DIR / f"{stem}_5m.json").read_text())
    raw15 = json.loads((CACHE_DIR / f"{stem}_15m.json").read_text())
    raw1h = json.loads((CACHE_DIR / f"{stem}_1h.json").read_text())
    _B5, _B15, _B1H = raw5["candles"], raw15["candles"], raw1h["candles"]


def _eval(ov):
    return run_bt(_B5, _B15, _B1H, ov, _MONTHS)


def main():
    b5, b15, b1h, months = load_cached_bars()
    # Discover stem for workers
    files = sorted(CACHE_DIR.glob("okx_BTCUSDT_180d_*_5m.json"), key=lambda p: p.stat().st_size)
    stem = files[-1].name.replace("_5m.json", "")
    t0 = time.time()

    # Focused grid around known high-WR short ASIA+LNDN skeleton
    cfgs = []
    for sessions in (["ASIA", "LNDN"], ["LNDN"], ["ASIA", "LNDN", "NYPM"], ["ASIA", "LNDN", "NYAM", "NYPM"]):
        for sides in (["short"], ["long", "short"]):
            for tp in (1.0, 1.2, 1.5, 1.8, 2.0):
                for mode in ("fixed_tp", "be_trail"):
                    for risk in (1.0, 1.25, 1.5, 1.75, 2.0):
                        for cd in (180, 300):
                            for long_max, short_min in ((55, 45), (60, 40), (58, 42)):
                                for lookback in (1, 2):
                                    for slb in (0.0003, 0.0006):
                                        for band in (None, (40, 65), (45, 70)):
                                            be = 0.5 if tp <= 1.2 else 0.75
                                            trail = 0.3 if mode == "be_trail" else 1.0
                                            cfgs.append(build_ov(
                                                sessions, sides, mode, tp, be, trail,
                                                long_max, short_min, risk, cd,
                                                body=0.0, lookback=lookback,
                                                sl_buffer=slb, rsi_band=band,
                                            ))

    # Dedup + prioritize
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
            s += 10
        en = tuple(w["name"] for w in ov["sessions"]["windows"] if w["enabled"])
        if en == ("ASIA", "LNDN"):
            s += 8
        if ov["exits"]["mode"] == "fixed_tp":
            s += 3
        if ov["risk"]["rr_target"] in (1.2, 1.5):
            s += 3
        if ov["risk"]["risk_pct_per_trade"] in (1.5, 1.75, 2.0):
            s += 2
        return -s

    uniq.sort(key=prio)
    # Cap for runtime — 6m bars are ~2x slower
    LIMIT = 160
    work = uniq[:LIMIT]
    print(f"Evaluating {len(work)}/{len(uniq)} configs on {months:.0f}m data…", flush=True)

    hits = []
    near = []
    results = []

    # Sequential is safer for memory with 50k bars; use 2 workers max
    workers = 2
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=(stem, months)) as ex:
        futs = {ex.submit(_eval, ov): i for i, ov in enumerate(work)}
        done = 0
        for fut in as_completed(futs):
            done += 1
            try:
                r = fut.result()
            except Exception as e:
                print("ERR", e, flush=True)
                continue
            results.append(r)
            ov = r["overrides"]
            tag = (
                f"wr={r['win_rate_pct']}% mo={r['per_month_simple_pct']}% "
                f"dd={r['max_dd_pct']}% day={r['worst_day_pct']}% n={r['trades']} "
                f"risk={ov['risk']['risk_pct_per_trade']} tp={ov['risk']['rr_target']} "
                f"sides={ov['_allowed_sides']} mode={ov['exits']['mode']}"
            )
            if r["passes"]:
                hits.append(r)
                print("HIT", tag, flush=True)
            elif r["win_rate_pct"] >= TARGET_WR and r["risk_ok"] and r["per_month_simple_pct"] >= 7:
                near.append(r)
                print("NEAR", tag, flush=True)
            if done % 10 == 0:
                print(f"… {done}/{len(work)} hits={len(hits)} near={len(near)} [{time.time()-t0:.0f}s]", flush=True)
            if len(hits) >= 5:
                print("Enough hits — cancelling remaining", flush=True)
                break

    # If no hit, try one more aggressive quality pass on best near-misses
    if not hits and near:
        print("Refining near-misses…", flush=True)
        seeds = sorted(near, key=lambda r: (r["per_month_simple_pct"], r["win_rate_pct"], -r["max_dd_pct"]), reverse=True)[:5]
        extra = []
        for seed in seeds:
            base = seed["overrides"]
            for risk in (1.25, 1.4, 1.5, 1.6, 1.75, 1.9, 2.0):
                for tp in (1.0, 1.2, 1.3, 1.5, 1.7):
                    for cd in (120, 180, 240, 300):
                        ov = copy.deepcopy(base)
                        ov["risk"]["risk_pct_per_trade"] = risk
                        ov["risk"]["rr_target"] = tp
                        ov["exits"]["tp_rr"] = tp
                        ov["entry"]["cooldown_seconds"] = cd
                        extra.append(ov)
        seen = set()
        extra_u = []
        for ov in extra:
            k = json.dumps(ov, sort_keys=True, default=str)
            if k in seen:
                continue
            seen.add(k)
            extra_u.append(ov)
        print(f"Refine {len(extra_u)}", flush=True)
        for i, ov in enumerate(extra_u[:80]):
            r = run_bt(b5, b15, b1h, ov, months)
            results.append(r)
            if r["passes"]:
                hits.append(r)
                print(
                    f"HIT wr={r['win_rate_pct']}% mo={r['per_month_simple_pct']}% "
                    f"dd={r['max_dd_pct']}% day={r['worst_day_pct']}% n={r['trades']}",
                    flush=True,
                )
            if i % 10 == 0:
                print(f"  refine {i} hits={len(hits)}", flush=True)

    def rank(r):
        return (
            1 if r["passes"] else 0,
            1 if r["risk_ok"] else 0,
            r["per_month_simple_pct"] if r["risk_ok"] else -999,
            r["win_rate_pct"],
            -r["max_dd_pct"],
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
            "period_months": months,
        },
        "hits": len(hits),
        "near": len(near),
        "elapsed_sec": round(time.time() - t0, 1),
        "top": [],
    }
    for r in top:
        row = {k: r[k] for k in r if k != "overrides"}
        row["overrides"] = r["overrides"]
        out["top"].append(row)

    path = ROOT / "data" / "tune_results_6m.json"
    path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nSaved {path} hits={len(hits)} [{time.time()-t0:.0f}s]", flush=True)
    if out["top"]:
        print(json.dumps(out["top"][0], indent=2, default=str)[:3500], flush=True)


if __name__ == "__main__":
    main()
