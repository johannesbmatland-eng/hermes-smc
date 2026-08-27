#!/usr/bin/env python3
"""
3-month BTC/USD backtest of the live hermes-smc strategy.

Reuses PaperTradingEngine.detect_entry_signal + check_exit_conditions
with rolling windows matching live fetch sizes. Session filter uses
bar timestamps (not wall clock).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ccxt

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hermes_smc.engine.analytics import (  # noqa: E402
    active_session,
    build_analytics,
    enrich_trade_meta,
    session_from_ts,
)
from hermes_smc.engine.paper_trading import PaperTradingEngine  # noqa: E402
from hermes_smc.engine.smc_engine import SMCConfig  # noqa: E402

CONFIG_PATH = ROOT / "hermes_smc" / "config" / "strategy.yaml"
CACHE_DIR = ROOT / "data" / "backtest_cache"


def _to_candles(ohlcv: list) -> list[dict]:
    return [
        {
            "timestamp": int(c[0] / 1000),
            "open": float(c[1]),
            "high": float(c[2]),
            "low": float(c[3]),
            "close": float(c[4]),
            "volume": float(c[5]),
        }
        for c in ohlcv
    ]


def fetch_ohlcv_range(
    symbol: str,
    timeframe: str,
    since_ms: int,
    until_ms: int,
    cache_path: Path,
    exchange_id: str = "binance",
) -> list[dict]:
    """
    Paginate OHLCV forward from since→until.

    Kraken's public OHLC only returns ~720 bars, so we default to OKX
    (BTC/USDT) for multi-month history. Signals are strategy-equivalent.
    """
    if cache_path.exists():
        raw = json.loads(cache_path.read_text())
        if (
            raw.get("since_ms") == since_ms
            and raw.get("until_ms") == until_ms
            and raw.get("exchange") == exchange_id
            and len(raw.get("candles") or []) > 1000
        ):
            print(f"  cache hit {cache_path.name}: {len(raw['candles'])} bars")
            return raw["candles"]

    exchange = getattr(ccxt, exchange_id)({"enableRateLimit": True})
    tf_ms = int(exchange.parse_timeframe(timeframe) * 1000)
    # OKX candles max 300; others often 1000
    limit = 300 if exchange_id == "okx" else 1000
    all_rows: list = []
    cursor = since_ms
    print(f"  fetching {exchange_id} {symbol} {timeframe} …")
    while cursor < until_ms:
        batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=limit)
        if not batch:
            break
        all_rows.extend(batch)
        last = batch[-1][0]
        nxt = last + tf_ms
        if nxt <= cursor:
            break
        cursor = nxt
        if len(all_rows) % 5000 < limit:
            print(
                f"    … {len(all_rows)} bars through "
                f"{datetime.fromtimestamp(last / 1000, tz=timezone.utc).isoformat()}"
            )
        if last >= until_ms - tf_ms:
            break

    by_ts = {int(r[0]): r for r in all_rows}
    rows = [by_ts[k] for k in sorted(by_ts) if since_ms <= k < until_ms]
    candles = _to_candles(rows)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({
        "since_ms": since_ms,
        "until_ms": until_ms,
        "symbol": symbol,
        "timeframe": timeframe,
        "exchange": exchange_id,
        "candles": candles,
    }))
    print(f"  saved {len(candles)} bars → {cache_path.name}")
    try:
        exchange.close()
    except Exception:
        pass
    return candles


def align_upto(candles: list[dict], ts: int) -> list[dict]:
    """Candles with timestamp <= ts."""
    # binary search
    lo, hi = 0, len(candles)
    while lo < hi:
        mid = (lo + hi) // 2
        if candles[mid]["timestamp"] <= ts:
            lo = mid + 1
        else:
            hi = mid
    return candles[:lo]


def try_exit_on_bar(engine: PaperTradingEngine, position: dict, bar: dict) -> str | None:
    """
    Path the bar for exits. Conservative if SL and TP both touched:
    assume stop first.
    """
    side = position.get("side", "long")
    # Update trail using favorable extreme first
    extreme = bar["high"] if side == "long" else bar["low"]
    engine._update_trailing_stops(position, extreme)
    # Also track peak/trough via ensure path used by check_exit
    engine._ensure_exit_plan(position)

    sl = float(position["stop_loss"])
    tp = position.get("take_profit")

    if side == "long":
        hit_sl = bar["low"] <= sl
        hit_tp = tp is not None and bar["high"] >= float(tp)
        if hit_sl and hit_tp:
            return "stop_loss"
        if hit_sl:
            return "trailing_stop" if position.get("sl_mode") in ("trailing", "break_even") else "stop_loss"
        if hit_tp:
            return "take_profit"
    else:
        hit_sl = bar["high"] >= sl
        hit_tp = tp is not None and bar["low"] <= float(tp)
        if hit_sl and hit_tp:
            return "stop_loss"
        if hit_sl:
            return "trailing_stop" if position.get("sl_mode") in ("trailing", "break_even") else "stop_loss"
        if hit_tp:
            return "take_profit"

    # Mark-to-market on close (trail may move on close too)
    engine._update_trailing_stops(position, bar["close"])
    return None


def exit_fill_price(position: dict, reason: str, bar: dict) -> float:
    side = position.get("side", "long")
    sl = float(position["stop_loss"])
    tp = position.get("take_profit")
    if reason in ("stop_loss", "trailing_stop"):
        return sl
    if reason == "take_profit" and tp is not None:
        return float(tp)
    return float(bar["close"])


def run_backtest(days: int = 90) -> dict:
    cfg = SMCConfig(CONFIG_PATH)
    # Wall-clock session gate inside detect_entry_signal must be off;
    # we apply bar-time session filter ourselves.
    sessions = dict(cfg.get("sessions", {}) or {})
    sessions["filter_entries"] = False
    cfg._config["sessions"] = sessions

    engine = PaperTradingEngine(cfg)
    # Isolated in-memory state (no Railway state.json)
    engine.position_manager.state_dir = None
    engine.position_manager.open_positions = {}
    engine.position_manager.closed_positions = []
    engine.position_manager.trade_history = []
    engine.position_manager.capital = float(cfg.get("paper_trading.initial_capital", 100_000))
    engine.position_manager.initial_capital = engine.position_manager.capital

    # Live bot uses Kraken BTC/USD; Kraken OHLC history is too short for
    # multi-month 5m bars, so we backtest on OKX BTC/USDT historical OHLCV.
    data_symbol = "BTC/USDT"
    data_exchange = "okx"
    market = cfg.get("market", "BTC/USD")
    until = datetime.now(tz=timezone.utc).replace(minute=0, second=0, microsecond=0)
    since = until - timedelta(days=days)
    since_ms = int(since.timestamp() * 1000)
    until_ms = int(until.timestamp() * 1000)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tag = f"okx_BTCUSDT_{days}d_{since.date()}_{until.date()}"
    bars_5m = fetch_ohlcv_range(
        data_symbol, "5m", since_ms, until_ms, CACHE_DIR / f"{tag}_5m.json", data_exchange
    )
    bars_15m = fetch_ohlcv_range(
        data_symbol, "15m", since_ms, until_ms, CACHE_DIR / f"{tag}_15m.json", data_exchange
    )
    bars_1h = fetch_ohlcv_range(
        data_symbol, "1h", since_ms, until_ms, CACHE_DIR / f"{tag}_1h.json", data_exchange
    )

    if len(bars_5m) < 250:
        raise RuntimeError(f"Not enough 5m data: {len(bars_5m)}")

    win_5m = 500
    win_15m = 200
    win_1h = 200
    cooldown = float(cfg.get("entry.cooldown_seconds", 300))
    session_cfg = cfg.get("sessions", {}) or {}
    max_open = int(cfg.get("entry.max_open_positions", 1))

    last_trade_ts = 0.0
    signals = 0
    skipped_session = 0
    t0 = time.time()

    # Start after warmup so EMA/RSI/FVG are meaningful
    start_i = max(win_5m, 120)
    for i in range(start_i, len(bars_5m)):
        bar = bars_5m[i]
        ts = bar["timestamp"]
        c5 = bars_5m[max(0, i + 1 - win_5m): i + 1]
        c15_all = align_upto(bars_15m, ts)
        c1h_all = align_upto(bars_1h, ts)
        c15 = c15_all[-win_15m:]
        c1h = c1h_all[-win_1h:]
        if len(c15) < 60 or len(c1h) < 60:
            continue

        # --- exits ---
        for trade_id, position in list(engine.position_manager.open_positions.items()):
            reason = try_exit_on_bar(engine, position, bar)
            if reason:
                fill = exit_fill_price(position, reason, bar)
                exit_rsi = engine._calc_rsi(c5, int(cfg.get("rsi.period", 14)))
                info = position.setdefault("strategy_info", {})
                info["session"] = info.get("session") or session_from_ts(
                    position.get("open_time"), session_cfg
                )
                engine.position_manager.close_position(
                    trade_id, fill, reason, rsi_at_exit=exit_rsi
                )
                last_trade_ts = ts

        if len(engine.position_manager.open_positions) >= max_open:
            continue
        if ts - last_trade_ts < cooldown:
            continue

        # Session filter on the locked confirmation candle time (matches entry fill bar)
        locked_ts = c5[-2]["timestamp"] if len(c5) >= 2 else ts
        if not active_session(ts=locked_ts, config=session_cfg):
            skipped_session += 1
            continue

        signal = engine.detect_entry_signal(c5, c15, c1h, candles_1m=None)
        if not signal or signal["position_size"] <= 0:
            continue

        signals += 1
        side = signal.get("side", "long")
        trade_id = str(uuid.uuid4())
        rsi = signal.get("rsi")
        sess = session_from_ts(locked_ts, session_cfg)
        engine.position_manager.open_position(
            trade_id=trade_id,
            asset=market,
            side=side,
            entry_price=signal["entry_price"],
            position_size=signal["position_size"],
            sl_price=signal["sl_price"],
            tp_price=signal["tp_price"],
            strategy_info={
                "fvg_bottom": signal["fvg"]["bottom"],
                "fvg_top": signal["fvg"]["top"],
                "confirmation": signal["confirmation"],
                "trend": signal["trend_info"]["overall"],
                "side": side,
                "paper_trade": True,
                "backtest": True,
                "session": sess,
                "rsi_at_entry": round(float(rsi), 2) if rsi is not None else None,
            },
        )
        # Stamp open_time to bar time for analytics
        pos = engine.position_manager.open_positions[trade_id]
        pos["open_time"] = float(locked_ts)
        if rsi is not None:
            pos["rsi_at_entry"] = round(float(rsi), 2)
        last_trade_ts = ts

        if (i - start_i) % 2000 == 0:
            elapsed = time.time() - t0
            print(
                f"  progress {i}/{len(bars_5m)} ({100*i/len(bars_5m):.0f}%) "
                f"closed={len(engine.position_manager.closed_positions)} "
                f"signals={signals} [{elapsed:.0f}s]"
            )

    # Force-close leftover at last close
    if bars_5m and engine.position_manager.open_positions:
        last = bars_5m[-1]
        for trade_id, position in list(engine.position_manager.open_positions.items()):
            exit_rsi = engine._calc_rsi(bars_5m[-win_5m:], int(cfg.get("rsi.period", 14)))
            engine.position_manager.close_position(
                trade_id, float(last["close"]), "end_of_backtest", rsi_at_exit=exit_rsi
            )

    closed = engine.position_manager.closed_positions
    initial = engine.position_manager.initial_capital
    analytics = build_analytics(closed, initial_capital=initial, session_config=session_cfg)
    enriched = [enrich_trade_meta(t, initial, session_cfg) for t in closed]

    wins = [t for t in enriched if float(t.get("pnl") or 0) > 0]
    losses = [t for t in enriched if float(t.get("pnl") or 0) <= 0]
    total_pnl = sum(float(t.get("pnl") or 0) for t in enriched)
    final_capital = initial + total_pnl

    by_reason: dict[str, int] = {}
    for t in enriched:
        r = t.get("exit_reason") or "unknown"
        by_reason[r] = by_reason.get(r, 0) + 1

    result = {
        "period_days": days,
        "from": since.isoformat(),
        "to": until.isoformat(),
        "market": market,
        "data_source": f"{data_exchange}:{data_symbol}",
        "bars_5m": len(bars_5m),
        "signals": signals,
        "trades": len(enriched),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": (100.0 * len(wins) / len(enriched)) if enriched else 0.0,
        "total_pnl": round(total_pnl, 2),
        "total_account_pct": round((total_pnl / initial) * 100, 3) if initial else 0.0,
        "initial_capital": initial,
        "final_capital": round(final_capital, 2),
        "avg_r": round(analytics.get("avg_r") or 0, 3),
        "avg_win": round(sum(float(t["pnl"]) for t in wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(float(t["pnl"]) for t in losses) / len(losses), 2) if losses else 0.0,
        "profit_factor": (
            round(
                abs(sum(float(t["pnl"]) for t in wins))
                / max(1e-9, abs(sum(float(t["pnl"]) for t in losses))),
                3,
            )
            if losses and wins
            else None
        ),
        "max_win": round(max((float(t["pnl"]) for t in wins), default=0), 2),
        "max_loss": round(min((float(t["pnl"]) for t in losses), default=0), 2),
        "by_exit_reason": by_reason,
        "by_session": analytics.get("by_session"),
        "by_side": analytics.get("by_side"),
        "by_rsi_entry": analytics.get("by_rsi_entry"),
        "best_session": analytics.get("best_session"),
        "elapsed_sec": round(time.time() - t0, 1),
        "skipped_session_bars": skipped_session,
        "strategy": {
            "exits": cfg.get("exits"),
            "rsi": cfg.get("rsi"),
            "sessions": {
                "windows": (cfg.get("sessions") or {}).get("windows"),
                "filter_entries": True,  # applied via bar-time active_session
            },
            "risk_pct": cfg.get("risk.risk_pct_per_trade"),
        },
        "recent_trades": [
            {
                "side": t.get("side"),
                "session": t.get("session"),
                "entry": t.get("entry_price"),
                "exit": t.get("exit_price"),
                "pnl": round(float(t.get("pnl") or 0), 2),
                "r": round(float(t.get("r_multiple") or 0), 2),
                "reason": t.get("exit_reason"),
                "rsi_in": t.get("rsi_at_entry"),
                "rsi_out": t.get("rsi_at_exit"),
            }
            for t in enriched[-15:]
        ],
    }
    return result


def main():
    parser = argparse.ArgumentParser(description="Hermes SMC 3-month backtest")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "data" / "backtest_3m_result.json",
    )
    args = parser.parse_args()
    print(f"Backtesting {args.days} days with {CONFIG_PATH} …")
    result = run_backtest(days=args.days)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, default=str))
    print("\n========== BACKTEST RESULT ==========")
    print(json.dumps({k: result[k] for k in result if k not in ("recent_trades", "strategy")}, indent=2))
    print("\nBy session:")
    for row in result.get("by_session") or []:
        print(
            f"  {row['name']}: n={row['trades']} win={row['win_rate']*100:.0f}% "
            f"pnl=${row['total_pnl']:.2f} avgR={row['avg_r']:.2f}"
        )
    print(f"\nSaved → {args.out}")


if __name__ == "__main__":
    main()
