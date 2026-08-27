"""Event-driven backtest with fees, slippage, and hard risk engine."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import ACCOUNT_EQUITY, PASS_PROFIT, STATES
from .markov_model import MarkovFit, classify_regimes, fit_markov
from .risk_engine import RiskEngine
from .strategy import (
    STRATEGY_STOP_PCT,
    STRATEGY_TP_PCT,
    MarkovStrategy,
    Position,
    Trade,
    apply_side_cost,
    unrealized_pnl,
)


@dataclass
class BacktestResult:
    equity_curve: pd.Series
    trades: list[Trade]
    risk: RiskEngine
    stats: dict
    daily_returns: pd.Series = field(default_factory=pd.Series)


def run_backtest(
    df: pd.DataFrame,
    fit: MarkovFit | None = None,
    start_i: int = 0,
    end_i: int | None = None,
    initial_equity: float = ACCOUNT_EQUITY,
    flatten_on_pass: bool = False,
    prop_mode: bool = False,
) -> BacktestResult:
    end_i = end_i if end_i is not None else len(df)
    if fit is None:
        fit = fit_markov(df.iloc[max(0, start_i - 5000) : start_i] if start_i > 1000 else df.iloc[:end_i])

    labels = classify_regimes(df)
    start_i = max(start_i, 60)

    strat = MarkovStrategy(fit)
    # sync prev hard from just before window
    if start_i > 0:
        strat.prev_hard = int(labels[start_i - 1])

    risk = RiskEngine(equity=initial_equity, pass_profit=PASS_PROFIT)
    equity = initial_equity
    cash = initial_equity
    equities = []
    idx_list = []

    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    ret = df["ret"].fillna(0.0).values
    ts = df["timestamp"].values
    hour = df["hour"].values
    cum3 = pd.Series(close).pct_change(3).values

    for i in range(start_i, end_i):
        day_key = pd.Timestamp(ts[i]).floor("D")
        risk.on_new_bar(day_key)

        price = float(close[i])
        hi = float(high[i])
        lo = float(low[i])

        if i > start_i:
            strat.update_belief(float(ret[i]))

        pos = strat.pos
        if pos.side != 0:
            pos.bars_held += 1
            # Wide protective stop: -4.5% from entry (portfolio soft-stops handle prop)
            hit_stop = lo <= pos.stop if pos.side > 0 else hi >= pos.stop
            hit_tp = hi >= pos.tp if pos.side > 0 else lo <= pos.tp
            exit_px = None
            reason = ""
            if hit_stop:
                exit_px = pos.stop
                reason = "stop"
            elif hit_tp:
                exit_px = pos.tp
                reason = "tp"
            elif pos.bars_held >= pos.max_hold:
                exit_px = price
                reason = "time"
            elif risk.state.halted_today or risk.state.failed:
                exit_px = price
                reason = "risk_halt"
            elif flatten_on_pass and risk.state.passed:
                exit_px = price
                reason = "prop_pass"

            if exit_px is not None:
                pnl = pos.notional * ((exit_px - pos.entry) / pos.entry)
                fee = apply_side_cost(pos.notional, exit_px)
                pnl -= fee
                cash += pnl
                strat.trades.append(
                    Trade(
                        entry_time=ts[i],
                        exit_time=ts[i],
                        side=pos.side,
                        entry=pos.entry,
                        exit=float(exit_px),
                        pnl=pnl,
                        state=pos.tag or (STATES[pos.entry_state] if pos.entry_state >= 0 else "?"),
                        reason=reason,
                    )
                )
                strat.pos = Position()
                strat.cooldown = 6
                pos = strat.pos

        # entries
        if pos.side == 0 and risk.allow_new_trade() and not (prop_mode and risk.state.passed):
            side, risk_frac, tag, hold = strat.signal(
                int(labels[i]),
                float(cum3[i]) if np.isfinite(cum3[i]) else 0.0,
                int(hour[i]),
                float(ret[i]),
            )
            if side != 0 and risk_frac > 0:
                notional = strat.size_notional(equity, risk_frac, side)
                notional = risk.clamp_notional(notional, price, equity)
                if abs(notional) > 0:
                    fee = apply_side_cost(notional, price)
                    cash -= fee
                    stop = price * (1.0 - STRATEGY_STOP_PCT)
                    tp = price * (1.0 + STRATEGY_TP_PCT)
                    strat.pos = Position(
                        side=side,
                        entry=price,
                        notional=notional,
                        stop=stop,
                        tp=tp,
                        bars_held=0,
                        entry_state=int(np.argmax(strat.posterior)),
                        max_hold=hold,
                        tag=tag,
                    )

        u = unrealized_pnl(strat.pos, price)
        equity = cash + u
        risk.update_equity(equity)
        equities.append(equity)
        idx_list.append(ts[i])

        if prop_mode and (risk.state.failed or risk.state.passed):
            if strat.pos.side != 0:
                pnl = unrealized_pnl(strat.pos, price) - apply_side_cost(strat.pos.notional, price)
                cash += pnl
                strat.pos = Position()
                equity = cash
                risk.update_equity(equity)
                equities[-1] = equity
            break

    eq = pd.Series(equities, index=pd.to_datetime(idx_list, utc=True), name="equity")
    stats = compute_stats(eq, strat.trades, risk)
    daily = eq.resample("1D").last().dropna().pct_change().dropna()
    return BacktestResult(equity_curve=eq, trades=strat.trades, risk=risk, stats=stats, daily_returns=daily)


def compute_stats(eq: pd.Series, trades: list[Trade], risk: RiskEngine) -> dict:
    rets = eq.pct_change().dropna()
    daily = eq.resample("1D").last().dropna().pct_change().dropna()
    pnls = np.array([t.pnl for t in trades], dtype=float) if trades else np.array([0.0])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    hitrate = float((pnls > 0).mean()) if len(pnls) else 0.0
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(abs(losses.mean())) if len(losses) else 1e-9
    payoff = avg_win / avg_loss if avg_loss > 0 else 0.0
    expectancy = float(pnls.mean()) if len(pnls) else 0.0

    mu = float(rets.mean()) if len(rets) else 0.0
    sig = float(rets.std()) if len(rets) else 1e-9
    sharpe = (mu / sig) * np.sqrt(24 * 365) if sig > 0 else 0.0
    downside = rets[rets < 0]
    dstd = float(downside.std()) if len(downside) else 1e-9
    sortino = (mu / dstd) * np.sqrt(24 * 365) if dstd > 0 else 0.0

    if len(daily) >= 5:
        eq_d = eq.resample("1D").last().dropna()
        months = []
        for i in range(30, len(eq_d)):
            months.append(float(eq_d.iloc[i] / eq_d.iloc[i - 30] - 1.0))
        monthly_mean = float(np.mean(months)) if months else float(eq.iloc[-1] / eq.iloc[0] - 1)
        monthly_med = float(np.median(months)) if months else monthly_mean
    else:
        monthly_mean = float(eq.iloc[-1] / eq.iloc[0] - 1) if len(eq) else 0.0
        monthly_med = monthly_mean

    peak = eq.cummax()
    dd = ((peak - eq) / peak).max() if len(eq) else 0.0

    return {
        "n_trades": int(len(trades)),
        "hitrate": hitrate,
        "payoff_ratio": float(payoff),
        "expectancy": expectancy,
        "sharpe": float(sharpe),
        "sortino": float(sortino),
        "max_dd": float(dd),
        "final_equity": float(eq.iloc[-1]) if len(eq) else ACCOUNT_EQUITY,
        "total_return": float(eq.iloc[-1] / eq.iloc[0] - 1) if len(eq) else 0.0,
        "monthly_profit_mean": monthly_mean,
        "monthly_profit_median": monthly_med,
        "max_daily_loss_observed": float(risk.state.max_daily_loss_obs),
        "max_dd_observed": float(risk.state.max_dd_obs),
        "max_leverage_used": float(risk.state.max_leverage_used),
        "passed": bool(risk.state.passed),
        "failed": bool(risk.state.failed),
        "fail_reason": risk.state.fail_reason,
        "breaches": dict(risk.state.breaches),
    }
