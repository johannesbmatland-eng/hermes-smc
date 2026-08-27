"""Trade analytics: sessions, market conditions, performance breakdowns."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

# Contiguous KZP-style sessions — each ends when the next begins (NY time)
DEFAULT_SESSIONS = {
    "timezone": "America/New_York",
    "filter_entries": True,
    "windows": [
        {"name": "ASIA", "start": "20:00", "end": "02:00", "enabled": True},
        {"name": "LNDN", "start": "02:00", "end": "09:30", "enabled": True},
        {"name": "NYAM", "start": "09:30", "end": "13:30", "enabled": True},
        {"name": "NYPM", "start": "13:30", "end": "20:00", "enabled": True},
    ],
}


def _parse_hhmm(value: str) -> int:
    """'09:30' → minutes from midnight."""
    parts = str(value).strip().split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    return hour * 60 + minute


def _in_window(minutes: int, start: str, end: str) -> bool:
    """Inclusive start, exclusive end. Supports midnight wrap (e.g. 20:00–00:00)."""
    start_m = _parse_hhmm(start)
    end_m = _parse_hhmm(end)
    if start_m == end_m:
        return False
    if start_m < end_m:
        return start_m <= minutes < end_m
    return minutes >= start_m or minutes < end_m


def _session_cfg(config: dict | None = None) -> dict:
    cfg = dict(DEFAULT_SESSIONS)
    if config:
        cfg["timezone"] = config.get("timezone", cfg["timezone"])
        if "filter_entries" in config:
            cfg["filter_entries"] = bool(config["filter_entries"])
        if config.get("windows"):
            cfg["windows"] = config["windows"]
    return cfg


def _local_minutes(ts: float, tz_name: str) -> int:
    tz = ZoneInfo(tz_name)
    local = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(tz)
    return local.hour * 60 + local.minute


def session_from_ts(ts: float | None, config: dict | None = None) -> str:
    """Map unix timestamp → KZP session name (NY time)."""
    if not ts:
        return "Unknown"
    cfg = _session_cfg(config)
    minutes = _local_minutes(float(ts), cfg["timezone"])
    for window in cfg["windows"]:
        if not window.get("enabled", True):
            continue
        if _in_window(minutes, window["start"], window["end"]):
            return window["name"]
    return "Off-session"


def active_session(ts: float | None = None, config: dict | None = None) -> str | None:
    """Return enabled session name if *now* (or ts) is inside a killzone, else None."""
    import time as _time
    name = session_from_ts(ts if ts is not None else _time.time(), config)
    if name in ("Unknown", "Off-session"):
        return None
    return name


def weekday_from_ts(ts: float | None, config: dict | None = None) -> str:
    if not ts:
        return "Unknown"
    tz_name = _session_cfg(config)["timezone"]
    tz = ZoneInfo(tz_name)
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(tz).strftime("%A")


def enrich_trade_meta(
    trade: dict,
    initial_capital: float = 100_000,
    session_config: dict | None = None,
) -> dict:
    """
    Attach session / calendar / account-% fields for analytics.
    Safe to call on already-closed historical trades (backfill).
    """
    out = dict(trade)
    open_ts = out.get("open_time") or out.get("timestamp")
    exit_ts = out.get("exit_time") or open_ts
    info = out.get("strategy_info") or {}

    out["session"] = (
        out.get("session")
        or info.get("session")
        or session_from_ts(open_ts, session_config)
    )
    out["weekday"] = (
        out.get("weekday")
        or info.get("weekday")
        or weekday_from_ts(open_ts, session_config)
    )
    out["trend"] = out.get("trend") or info.get("trend") or "unknown"
    out["confirmation"] = (
        out.get("confirmation")
        or info.get("confirmation")
        or out.get("exit_reason")
        or "unknown"
    )
    out["side"] = out.get("side") or info.get("side") or "unknown"

    if out.get("rsi_at_entry") is None and info.get("rsi_at_entry") is not None:
        out["rsi_at_entry"] = info.get("rsi_at_entry")
    if out.get("rsi_at_exit") is None and info.get("rsi_at_exit") is not None:
        out["rsi_at_exit"] = info.get("rsi_at_exit")

    pnl = float(out.get("pnl") or 0)
    entry = float(out.get("entry_price") or 0)
    sl = float(out.get("stop_loss") or 0)
    size = float(out.get("position_size") or 0)

    baseline = float(out.get("capital_at_open") or initial_capital) or initial_capital
    out["pnl_account_pct"] = (pnl / baseline) * 100 if baseline else 0.0

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
    rows.sort(key=lambda r: (r["total_account_pct"], r["win_rate"], r["trades"]), reverse=True)
    return rows


def build_analytics(
    closed_positions: list[dict],
    initial_capital: float = 100_000,
    open_positions: list[dict] | None = None,
    session_config: dict | None = None,
) -> dict[str, Any]:
    """Aggregate performance by session, weekday, trend, confirmation, side, exit."""
    enriched = [
        enrich_trade_meta(t, initial_capital, session_config) for t in closed_positions
    ]
    open_enriched = [
        enrich_trade_meta(t, initial_capital, session_config)
        for t in (open_positions or [])
    ]

    total_pnl = sum(float(t.get("pnl") or 0) for t in enriched)
    total_account_pct = (total_pnl / initial_capital * 100) if initial_capital else 0.0
    wins = sum(1 for t in enriched if float(t.get("pnl") or 0) > 0)

    by_session = _bucket_stats(enriched, "session")
    by_weekday = _bucket_stats(enriched, "weekday")
    by_trend = _bucket_stats(enriched, "trend")
    by_confirmation = _bucket_stats(enriched, "confirmation")
    by_side = _bucket_stats(enriched, "side")
    by_exit = _bucket_stats(enriched, "exit_reason")

    for t in enriched:
        t["rsi_entry_bucket"] = rsi_bucket(t.get("rsi_at_entry"))
        t["rsi_exit_bucket"] = rsi_bucket(t.get("rsi_at_exit"))
    by_rsi_entry = _bucket_stats(enriched, "rsi_entry_bucket")
    by_rsi_exit = _bucket_stats(enriched, "rsi_exit_bucket")

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
        "by_rsi_entry": by_rsi_entry,
        "by_rsi_exit": by_rsi_exit,
        "best_session": best_session,
        "best_weekday": best_weekday,
        "best_condition": best_condition,
        "recent_enriched": enriched[-20:],
        "enough_data": len(enriched) >= 5,
        "session_timezone": _session_cfg(session_config)["timezone"],
        "note": (
            "Breakdowns get meaningful after ~1 week / 5+ closed trades."
            if len(enriched) < 5
            else "KZP session stats from closed paper trades (America/New_York)."
        ),
    }


def build_entry_context(
    trend_info: dict | None,
    confirmation: str | None,
    session_config: dict | None = None,
    rsi: float | None = None,
) -> dict:
    """Snapshot market context stored on the position at entry."""
    trend_info = trend_info or {}
    details = trend_info.get("details") or {}
    tz_name = _session_cfg(session_config)["timezone"]
    now = datetime.now(tz=ZoneInfo(tz_name))
    ts = now.timestamp()
    ctx = {
        "session": session_from_ts(ts, session_config),
        "weekday": weekday_from_ts(ts, session_config),
        "trend": trend_info.get("overall", "unknown"),
        "trend_5m": trend_info.get("trend_5m"),
        "trend_15m": trend_info.get("trend_15m"),
        "trend_1h": trend_info.get("trend_1h"),
        "confirmed_uptrend": details.get("confirmed_uptrend"),
        "confirmed_downtrend": details.get("confirmed_downtrend"),
        "confirmation": confirmation,
        "local_time": now.strftime("%H:%M"),
        "timezone": tz_name,
    }
    if rsi is not None:
        ctx["rsi_at_entry"] = round(float(rsi), 2)
    return ctx


def rsi_bucket(rsi: float | None) -> str:
    """Coarse RSI bucket for analytics (entry or exit)."""
    if rsi is None:
        return "unknown"
    v = float(rsi)
    if v < 30:
        return "RSI <30"
    if v < 40:
        return "RSI 30-40"
    if v < 50:
        return "RSI 40-50"
    if v < 60:
        return "RSI 50-60"
    if v < 70:
        return "RSI 60-70"
    return "RSI ≥70"
