"""Event-driven backtester for A+ 4H macro-flow breakouts."""

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
from .risk import RiskEngine
from .strategy_4h import SignalEvent, generate_a_plus_events, expected_R


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
    mode: str
    lookback: int


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
    soft_halt_count: int
    max_daily_loss_observed: float = 0.0
    max_dd_observed: float = 0.0
    max_leverage_used: float = 0.0


def _slip(px: float, side: int, is_entry: bool, one_way: float) -> float:
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
    events: list[SignalEvent] | None = None,
) -> BacktestResult:
    p = params or DEFAULT_PARAMS
    costs = costs or DEFAULT_COSTS
    prop = prop or DEFAULT_PROP
    one_way = costs.one_way_frac
    if events is None:
        events = generate_a_plus_events(df, p, costs)

    risk = RiskEngine(
        prop,
        start_equity=start_equity,
        use_soft_stops=enforce_prop_halt,
        enforce_hard_halt=enforce_prop_halt,
    )
    cash = float(start_equity if start_equity is not None else prop.account_usd)
    signed_qty = 0.0
    side = 0
    entry_px = 0.0
    stop = 0.0
    entry_i = -1
    entry_time = None
    entry_stop_dist = 0.0
    entry_mode = ""
    entry_lb = 0
    soft_halts = 0

    # Map bar index -> list of events to fire at that open
    pending: dict[int, list] = {}
    for e in events:
        pending.setdefault(e.bar_idx + 1, []).append(e)

    trades: list[Trade] = []
    eq_rows: list[dict[str, Any]] = []
    n = len(df)

    def mark_eq(px: float) -> float:
        return cash + signed_qty * px

    for i in range(n):
        row = df.iloc[i]
        ts = pd.Timestamp(row["timestamp"])
        day_key = str(ts.date())
        o = float(row["open"])
        h = float(row["high"])
        l = float(row["low"])
        c = float(row["close"])

        # Entry (one position at a time)
        if signed_qty == 0.0 and risk.can_open() and i in pending and pending[i]:
            e = pending[i].pop(0)
            atr = e.atr
            if atr > 0:
                fill = _slip(o, e.side, True, one_way)
                stop_dist = p.stop_atr_mult * atr
                eq0 = mark_eq(fill)
                dd = risk.state.drawdown_from_hwm
                risk_frac = p.risk_frac_equity * (0.5 if dd >= 0.02 else 1.0)
                qty = (eq0 * risk_frac) / stop_dist
                max_n = risk.max_notional(eq0, p.max_leverage)
                if qty * fill > max_n:
                    qty = max_n / fill if fill > 0 else 0.0
                if qty > 0:
                    signed_qty = qty if e.side > 0 else -qty
                    cash -= signed_qty * fill
                    side = e.side
                    entry_px = fill
                    entry_i = i
                    entry_time = ts
                    entry_stop_dist = stop_dist
                    entry_mode = e.mode
                    entry_lb = e.lookback
                    stop = fill - stop_dist if side > 0 else fill + stop_dist

        # Exit management (not on entry bar — avoid OHLC pathing vs entry open)
        if signed_qty != 0.0 and i > entry_i:
            exit_reason = None
            exit_raw = None
            bars_held = i - entry_i
            atr_trail = entry_stop_dist / p.stop_atr_mult if p.stop_atr_mult else 0.0
            if side > 0:
                if l <= stop:
                    exit_reason, exit_raw = "stop", stop
                else:
                    if p.use_trail and c >= entry_px + entry_stop_dist:
                        stop = max(stop, c - p.trail_atr_mult * atr_trail)
                        if l <= stop:
                            exit_reason, exit_raw = "trail", stop
                    if exit_reason is None and bars_held >= p.time_stop_bars:
                        exit_reason, exit_raw = "time", c
            else:
                if h >= stop:
                    exit_reason, exit_raw = "stop", stop
                else:
                    if p.use_trail and c <= entry_px - entry_stop_dist:
                        stop = min(stop, c + p.trail_atr_mult * atr_trail)
                        if h >= stop:
                            exit_reason, exit_raw = "trail", stop
                    if exit_reason is None and bars_held >= p.time_stop_bars:
                        exit_reason, exit_raw = "time", c

            if exit_reason is not None:
                fill = _slip(float(exit_raw), side, False, one_way)
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
                        mode=entry_mode,
                        lookback=entry_lb,
                    )
                )
                signed_qty = 0.0
                side = 0

        eq = mark_eq(c)
        notional = abs(signed_qty) * c
        risk.on_bar(day_key, eq, notional)

        # Soft halt: flatten and stop new trades
        if enforce_prop_halt and risk.state.soft_halted and signed_qty != 0.0:
            fill = _slip(c, 1 if signed_qty > 0 else -1, False, one_way)
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
                    reason=f"soft_{risk.state.soft_reason}",
                    fees=fee_est,
                    mode=entry_mode,
                    lookback=entry_lb,
                )
            )
            signed_qty = 0.0
            side = 0
            soft_halts += 1

        if enforce_prop_halt and risk.state.halted and signed_qty != 0.0:
            fill = _slip(c, 1 if signed_qty > 0 else -1, False, one_way)
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
                    mode=entry_mode,
                    lookback=entry_lb,
                )
            )
            signed_qty = 0.0
            side = 0

        eq_rows.append({"timestamp": ts, "equity": mark_eq(c), "side": side, "halted": risk.state.halted or risk.state.soft_halted})

    eq_df = pd.DataFrame(eq_rows)
    stats = summarize(eq_df, trades, prop)
    stats["expected_R_prior"] = expected_R(p)
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
        soft_halt_count=soft_halts + (1 if risk.state.soft_halted else 0),
        max_daily_loss_observed=risk.state.max_daily_loss_observed,
        max_dd_observed=risk.state.max_dd_observed,
        max_leverage_used=risk.state.max_leverage_used,
    )


def summarize(eq_df: pd.DataFrame, trades: list[Trade], prop: PropRules) -> dict[str, Any]:
    if eq_df.empty:
        return {"n_trades": 0}
    eq = eq_df["equity"].to_numpy(dtype=np.float64)
    start = float(eq[0])
    end = float(eq[-1])
    rets = np.diff(eq) / np.maximum(eq[:-1], 1e-12)
    rets = rets[np.isfinite(rets)]
    # 4H bars → ~6 per day
    ann = np.sqrt(6 * 365)
    sharpe = float(np.mean(rets) / np.std(rets) * ann) if len(rets) > 2 and np.std(rets) > 0 else 0.0
    downside = rets[rets < 0]
    ds = float(np.std(downside)) if len(downside) else 0.0
    sortino = float(np.mean(rets) / ds * ann) if ds > 0 else 0.0
    hwm = np.maximum.accumulate(eq)
    max_dd = float(np.max((hwm - eq) / np.maximum(hwm, 1e-12))) if len(eq) else 0.0

    pnls = np.array([t.pnl for t in trades], dtype=np.float64) if trades else np.array([])
    wins = pnls[pnls > 0] if len(pnls) else np.array([])
    losses = pnls[pnls <= 0] if len(pnls) else np.array([])
    hit = float(len(wins) / len(pnls)) if len(pnls) else 0.0
    avg_win = float(np.mean(wins)) if len(wins) else 0.0
    avg_loss = float(np.mean(np.abs(losses))) if len(losses) else 0.0
    payoff = (avg_win / avg_loss) if avg_loss > 0 else 0.0
    rs = [t.pnl_R for t in trades]
    exp_R = float(np.mean(rs)) if rs else 0.0

    t0 = pd.Timestamp(eq_df["timestamp"].iloc[0])
    t1 = pd.Timestamp(eq_df["timestamp"].iloc[-1])
    months = max((t1 - t0).total_seconds() / (30.4375 * 24 * 3600), 1e-6)
    total_ret = (end / start) - 1.0
    monthly_ret = (1.0 + total_ret) ** (1.0 / months) - 1.0 if start > 0 and total_ret > -1 else -1.0

    return {
        "n_trades": len(trades),
        "hit_rate": hit,
        "payoff_avg_win_loss": payoff,
        "expectancy_usd": float(np.mean(pnls)) if len(pnls) else 0.0,
        "expectancy_R": exp_R,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_dd": max_dd,
        "total_return": total_ret,
        "monthly_return_geo": monthly_ret,
        "trades_per_month": len(trades) / months,
        "start_equity": start,
        "end_equity": end,
        "months": months,
        "avg_win_usd": avg_win,
        "avg_loss_usd": avg_loss,
        "fees_total": float(sum(t.fees for t in trades)),
        "pass_equity_threshold": end >= prop.account_usd * (1.0 + prop.pass_pct),
        "modes": {
            "direct": sum(1 for t in trades if t.mode == "direct"),
            "pullback": sum(1 for t in trades if t.mode == "pullback"),
        },
    }
