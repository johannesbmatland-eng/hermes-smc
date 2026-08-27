"""Event-driven backtester with fees, slippage, and hard prop risk."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from .config import (
    CostModel,
    PropRules,
    StrategyParams,
    DEFAULT_COSTS,
    DEFAULT_PARAMS,
    DEFAULT_PROP,
)
from .features import build_features
from .risk import RiskEngine


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    side: int
    entry_px: float
    exit_px: float
    qty: float
    pnl: float
    pnl_R: float
    reason: str
    fees: float


@dataclass
class BacktestResult:
    equity_curve: pd.DataFrame
    trades: list[Trade]
    stats: dict[str, Any]
    risk_events: list[str]
    passed_prop: bool
    daily_breach: bool
    dd_breach: bool
    leverage_breach: bool


def _apply_slip(px: float, side: int, is_entry: bool, one_way: float) -> float:
    if side > 0:
        return px * (1.0 + one_way) if is_entry else px * (1.0 - one_way)
    return px * (1.0 - one_way) if is_entry else px * (1.0 + one_way)


def run_backtest(
    df: pd.DataFrame,
    params: StrategyParams | None = None,
    costs: CostModel | None = None,
    prop: PropRules | None = None,
    start_equity: float | None = None,
    enforce_prop_halt: bool = True,
) -> BacktestResult:
    from .signals import compute_signal_arrays

    p = params or DEFAULT_PARAMS
    costs = costs or DEFAULT_COSTS
    prop = prop or DEFAULT_PROP
    feat = build_features(df, p)
    sig = compute_signal_arrays(feat, p, costs)
    risk = RiskEngine(prop, start_equity=start_equity)
    one_way = costs.one_way_frac
    n = len(feat)

    cash = float(start_equity if start_equity is not None else prop.account_usd)
    signed_qty = 0.0
    side = 0
    entry_px = 0.0
    stop = 0.0
    target = 0.0
    entry_i = -1
    entry_time = None
    entry_stop_dist = 0.0
    cooldown_until = -1
    pending_side = 0

    trades: list[Trade] = []
    eq_rows: list[dict[str, Any]] = []

    def mark_eq(px: float) -> float:
        return cash + signed_qty * px

    for i in range(n):
        row = feat.iloc[i]
        ts = row["timestamp"]
        day_key = str(pd.Timestamp(ts).date())
        o = float(row["open"])
        h = float(row["high"])
        l = float(row["low"])
        c = float(row["close"])
        atr = float(row["atr"]) if np.isfinite(row["atr"]) else np.nan

        # Execute pending entry at open
        if pending_side != 0 and signed_qty == 0.0 and risk.can_open():
            if np.isfinite(atr) and atr > 0:
                fill = _apply_slip(o, pending_side, is_entry=True, one_way=one_way)
                stop_dist = p.stop_atr_mult * atr
                eq0 = mark_eq(fill)
                risk_usd = eq0 * p.risk_frac_equity
                qty = risk_usd / stop_dist if stop_dist > 0 else 0.0
                max_n = risk.max_notional(eq0, p.max_leverage)
                if qty * fill > max_n:
                    qty = max_n / fill if fill > 0 else 0.0
                if qty > 0:
                    signed_qty = qty if pending_side > 0 else -qty
                    cash -= signed_qty * fill
                    side = pending_side
                    entry_px = fill
                    entry_i = i
                    entry_time = ts
                    entry_stop_dist = stop_dist
                    if side > 0:
                        stop = fill - stop_dist
                        target = fill + p.target_atr_mult * atr
                    else:
                        stop = fill + stop_dist
                        target = fill - p.target_atr_mult * atr
                    cooldown_until = i + p.cooldown_bars
            pending_side = 0
        else:
            pending_side = 0

        # Manage exits
        if signed_qty != 0.0:
            exit_reason = None
            exit_raw = None
            bars_held = i - entry_i
            if side > 0:
                if l <= stop:
                    exit_reason, exit_raw = "stop", stop
                elif h >= target:
                    exit_reason, exit_raw = "target", target
                else:
                    if p.use_trail and np.isfinite(atr) and c >= entry_px + entry_stop_dist:
                        stop = max(stop, c - p.trail_atr_mult * atr)
                        if l <= stop:
                            exit_reason, exit_raw = "trail", stop
                    if exit_reason is None and bars_held >= p.time_stop_bars:
                        exit_reason, exit_raw = "time", c
            else:
                if h >= stop:
                    exit_reason, exit_raw = "stop", stop
                elif l <= target:
                    exit_reason, exit_raw = "target", target
                else:
                    if p.use_trail and np.isfinite(atr) and c <= entry_px - entry_stop_dist:
                        stop = min(stop, c + p.trail_atr_mult * atr)
                        if h >= stop:
                            exit_reason, exit_raw = "trail", stop
                    if exit_reason is None and bars_held >= p.time_stop_bars:
                        exit_reason, exit_raw = "time", c

            if exit_reason is not None:
                fill = _apply_slip(float(exit_raw), side, is_entry=False, one_way=one_way)
                cash += signed_qty * fill
                pnl = signed_qty * (fill - entry_px)
                fee_est = abs(signed_qty) * (entry_px + fill) * one_way
                pnl_R = pnl / (abs(signed_qty) * entry_stop_dist) if entry_stop_dist > 0 else 0.0
                trades.append(
                    Trade(
                        entry_time=entry_time,
                        exit_time=ts,
                        side=side,
                        entry_px=entry_px,
                        exit_px=fill,
                        qty=abs(signed_qty),
                        pnl=pnl,
                        pnl_R=pnl_R,
                        reason=exit_reason,
                        fees=fee_est,
                    )
                )
                signed_qty = 0.0
                side = 0
                entry_px = 0.0
                cooldown_until = max(cooldown_until, i + p.cooldown_bars)

        eq = mark_eq(c)
        notional = abs(signed_qty) * c
        risk.on_bar(day_key, eq, notional)

        if enforce_prop_halt and risk.state.halted and signed_qty != 0.0:
            fill = _apply_slip(c, 1 if signed_qty > 0 else -1, is_entry=False, one_way=one_way)
            cash += signed_qty * fill
            pnl = signed_qty * (fill - entry_px)
            fee_est = abs(signed_qty) * (entry_px + fill) * one_way
            pnl_R = pnl / (abs(signed_qty) * entry_stop_dist) if entry_stop_dist > 0 else 0.0
            trades.append(
                Trade(
                    entry_time=entry_time,
                    exit_time=ts,
                    side=1 if signed_qty > 0 else -1,
                    entry_px=entry_px,
                    exit_px=fill,
                    qty=abs(signed_qty),
                    pnl=pnl,
                    pnl_R=pnl_R,
                    reason=f"halt_{risk.state.halt_reason}",
                    fees=fee_est,
                )
            )
            signed_qty = 0.0
            side = 0

        eq_rows.append(
            {
                "timestamp": ts,
                "equity": mark_eq(c),
                "side": side,
                "halted": risk.state.halted,
            }
        )

        if signed_qty == 0.0 and (not risk.state.halted) and i >= cooldown_until and i + 1 < n:
            if sig["a_plus"][i]:
                pending_side = int(sig["side"][i])

    eq_df = pd.DataFrame(eq_rows)
    stats = summarize(eq_df, trades, prop)
    failed = risk.state.daily_breach or risk.state.dd_breach or risk.state.leverage_breach
    return BacktestResult(
        equity_curve=eq_df,
        trades=trades,
        stats=stats,
        risk_events=list(risk.state.events),
        passed_prop=(not failed) and risk.passed_challenge(),
        daily_breach=risk.state.daily_breach,
        dd_breach=risk.state.dd_breach,
        leverage_breach=risk.state.leverage_breach,
    )


def summarize(eq_df: pd.DataFrame, trades: list[Trade], prop: PropRules) -> dict[str, Any]:
    if eq_df.empty:
        return {"n_trades": 0}
    eq = eq_df["equity"].to_numpy(dtype=np.float64)
    start = float(eq[0])
    end = float(eq[-1])
    rets = np.diff(eq) / np.maximum(eq[:-1], 1e-12)
    rets = rets[np.isfinite(rets)]
    sharpe = 0.0
    sortino = 0.0
    if len(rets) > 2 and np.std(rets) > 0:
        sharpe = float(np.mean(rets) / np.std(rets) * np.sqrt(24 * 365))
        downside = rets[rets < 0]
        ds = float(np.std(downside)) if len(downside) else 0.0
        sortino = float(np.mean(rets) / ds * np.sqrt(24 * 365)) if ds > 0 else 0.0
    hwm = np.maximum.accumulate(eq)
    dd = (hwm - eq) / np.maximum(hwm, 1e-12)
    max_dd = float(np.max(dd)) if len(dd) else 0.0

    pnls = np.array([t.pnl for t in trades], dtype=np.float64) if trades else np.array([])
    wins = pnls[pnls > 0] if len(pnls) else np.array([])
    losses = pnls[pnls <= 0] if len(pnls) else np.array([])
    hit = float(len(wins) / len(pnls)) if len(pnls) else 0.0
    avg_win = float(np.mean(wins)) if len(wins) else 0.0
    avg_loss = float(np.mean(np.abs(losses))) if len(losses) else 0.0
    payoff = (avg_win / avg_loss) if avg_loss > 0 else 0.0
    expectancy = float(np.mean(pnls)) if len(pnls) else 0.0
    rs = [t.pnl_R for t in trades]
    exp_R = float(np.mean(rs)) if rs else 0.0

    t0 = pd.Timestamp(eq_df["timestamp"].iloc[0])
    t1 = pd.Timestamp(eq_df["timestamp"].iloc[-1])
    months = max((t1 - t0).total_seconds() / (30.4375 * 24 * 3600), 1e-6)
    total_ret = (end / start) - 1.0
    monthly_ret = (1.0 + total_ret) ** (1.0 / months) - 1.0 if start > 0 and total_ret > -1 else -1.0
    trades_per_month = len(trades) / months

    return {
        "n_trades": len(trades),
        "hit_rate": hit,
        "payoff_avg_win_loss": payoff,
        "expectancy_usd": expectancy,
        "expectancy_R": exp_R,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_dd": max_dd,
        "total_return": total_ret,
        "monthly_return_geo": monthly_ret,
        "trades_per_month": trades_per_month,
        "start_equity": start,
        "end_equity": end,
        "months": months,
        "avg_win_usd": avg_win,
        "avg_loss_usd": avg_loss,
        "fees_total": float(sum(t.fees for t in trades)),
        "pass_equity_threshold": end >= prop.account_usd * (1.0 + prop.pass_pct),
    }
