"""Trade analytics: sessions, market conditions, performance breakdowns."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any


# Crypto sessions in UTC (inclusive start, exclusive end for primary label)
SESSION_WINDOWS = (
    ("Asia", 0, 8),
    ("London", 8, 13),
    ("NY", 13, 21),
    ("Off-hours", 21, 24),
)


def session_from_ts(ts: float | None) -> str:
    """Map unix timestamp → trading session (UTC)."""
    if not ts:
        return "Unknown"
    hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
    # London/NY overlap gets its own bucket (often highest liquidity)
    if 12 <= hour < 16:
        return "London/NY overlap"
    for name, start, end in SESSION_WINDOWS:
        if start <= hour < end:
            return name
    return "Unknown"


def weekday_from_ts(ts: float | None) -> str:
    if not ts:
        return "Unknown"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%A")


def enrich_trade_meta(trade: dict, initial_capital: float = 100_000) -> dict:
    """
    Attach session / calendar / account-% fields for analytics.
    Safe to call on already-closed historical trades (backfill).
    """
    out = dict(trade)
    open_ts = out.get("open_time") or out.get("timestamp")
    exit_ts = out.get("exit_time") or open_ts
    info = out.get("strategy_info") or {}

    out["session"] = out.get("session") or info.get("session") or session_from_ts(open_ts)
    out["weekday"] = out.get("weekday") or info.get("weekday") or weekday_from_ts(open_ts)
    out["trend"] = out.get("trend") or info.get("trend") or "unknown"
    out["confirmation"] = (
        out.get("confirmation")
        or info.get("confirmation")
        or out.get("exit_reason")
        or "unknown"
    )
    out["side"] = out.get("side") or info.get("side") or "unknown"

    pnl = float(out.get("pnl") or 0)
    entry = float(out.get("entry_price") or 0)
    sl = float(out.get("stop_loss") or 0)
    size = float(out.get("position_size") or 0)

    # Account return vs starting capital (what "0.5% risk → 1% win" means)
    baseline = float(out.get("capital_at_open") or initial_capital) or initial_capital
    out["pnl_account_pct"] = (pnl / baseline) * 100 if baseline else 0.0

    # Price move % (kept for chart context)
    if out.get("pnl_pct") is None and entry and out.get("exit_price") is not None:
        exit_px = float(out["exit_price"])
        if out["side"] == "short":
            out["pnl_pct"] = (entry - exit_px) / entry * 100
        else:
            out["pnl_pct"] = (exit_px - entry) / entry * 100

    risk_dist = abs(entry - sl)
    risk_amount = risk_dist * size if risk_dist > 0 and size > 0 else 0.0
    out["risk_amount"] = risk_amount
    out["r_multiple"] = (pnl / risk_amount) if risk_amount > 0 else 0.0

    if open_ts and exit_ts:
        out["hold_minutes"] = max(0.0, (float(exit_ts) - float(open_ts)) / 60.0)
    else:
        out["hold_minutes"] = None

    return out


def _bucket_stats(trades: list[dict], key: str) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        groups[str(t.get(key) or "unknown")].append(t)

    rows = []
    for name, items in groups.items():
        pnls = [float(x.get("pnl") or 0) for x in items]
        wins = sum(1 for p in pnls if p > 0)
        account_pcts = [float(x.get("pnl_account_pct") or 0) for x in items]
        r_mults = [float(x.get("r_multiple") or 0) for x in items]
        n = len(items)
        rows.append({
            "name": name,
            "trades": n,
            "wins": wins,
            "losses": n - wins,
            "win_rate": (wins / n) if n else 0.0,
            "total_pnl": sum(pnls),
            "avg_pnl": (sum(pnls) / n) if n else 0.0,
            "total_account_pct": sum(account_pcts),
            "avg_account_pct": (sum(account_pcts) / n) if n else 0.0,
            "avg_r": (sum(r_mults) / n) if n else 0.0,
        })
    # Best first: total account % then win rate
    rows.sort(key=lambda r: (r["total_account_pct"], r["win_rate"], r["trades"]), reverse=True)
    return rows


def build_analytics(
    closed_positions: list[dict],
    initial_capital: float = 100_000,
    open_positions: list[dict] | None = None,
) -> dict[str, Any]:
    """Aggregate performance by session, weekday, trend, confirmation, side, exit."""
    enriched = [enrich_trade_meta(t, initial_capital) for t in closed_positions]
    open_enriched = [enrich_trade_meta(t, initial_capital) for t in (open_positions or [])]

    total_pnl = sum(float(t.get("pnl") or 0) for t in enriched)
    total_account_pct = (total_pnl / initial_capital * 100) if initial_capital else 0.0
    wins = sum(1 for t in enriched if float(t.get("pnl") or 0) > 0)

    by_session = _bucket_stats(enriched, "session")
    by_weekday = _bucket_stats(enriched, "weekday")
    by_trend = _bucket_stats(enriched, "trend")
    by_confirmation = _bucket_stats(enriched, "confirmation")
    by_side = _bucket_stats(enriched, "side")
    by_exit = _bucket_stats(enriched, "exit_reason")

    best_session = by_session[0] if by_session else None
    best_weekday = by_weekday[0] if by_weekday else None
    best_condition = None
    condition_candidates = by_trend + by_confirmation
    if condition_candidates:
        best_condition = max(
            condition_candidates,
            key=lambda r: (r["total_account_pct"], r["win_rate"], r["trades"]),
        )

    return {
        "trade_count": len(enriched),
        "open_count": len(open_enriched),
        "wins": wins,
        "losses": len(enriched) - wins,
        "win_rate": (wins / len(enriched)) if enriched else 0.0,
        "total_pnl": total_pnl,
        "total_account_pct": total_account_pct,
        "avg_r": (
            sum(float(t.get("r_multiple") or 0) for t in enriched) / len(enriched)
            if enriched else 0.0
        ),
        "by_session": by_session,
        "by_weekday": by_weekday,
        "by_trend": by_trend,
        "by_confirmation": by_confirmation,
        "by_side": by_side,
        "by_exit_reason": by_exit,
        "best_session": best_session,
        "best_weekday": best_weekday,
        "best_condition": best_condition,
        "recent_enriched": enriched[-20:],
        "enough_data": len(enriched) >= 5,
        "note": (
            "Breakdowns get meaningful after ~1 week / 5+ closed trades."
            if len(enriched) < 5
            else "Session and condition stats from closed paper trades."
        ),
    }


def build_entry_context(trend_info: dict | None, confirmation: str | None) -> dict:
    """Snapshot market context stored on the position at entry."""
    trend_info = trend_info or {}
    details = trend_info.get("details") or {}
    now = datetime.now(tz=timezone.utc)
    ts = now.timestamp()
    return {
        "session": session_from_ts(ts),
        "weekday": weekday_from_ts(ts),
        "trend": trend_info.get("overall", "unknown"),
        "trend_5m": trend_info.get("trend_5m"),
        "trend_15m": trend_info.get("trend_15m"),
        "trend_1h": trend_info.get("trend_1h"),
        "confirmed_uptrend": details.get("confirmed_uptrend"),
        "confirmed_downtrend": details.get("confirmed_downtrend"),
        "confirmation": confirmation,
        "utc_hour": now.hour,
    }
