"""100 randomized-start prop evaluation simulations."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from .backtest import run_backtest
from .config import (
    ACCOUNT_EQUITY,
    FEES_BPS,
    REPORTS_DIR,
    SLIPPAGE_BPS,
    SimConfig,
)
from .markov_model import fit_markov


def run_prop_100(
    df: pd.DataFrame,
    cfg: SimConfig | None = None,
    fit_end_frac: float = 0.70,
) -> dict:
    """
    Method:
    1. Fit Markov model on first fit_end_frac of data (in-sample).
    2. Draw 100 start indices uniformly from OOS region such that a full
       prop_window_hours slice fits.
    3. Each run: $100k account, prop_mode=True (stop on pass/fail).
    4. Pass = +10% without daily -3% or DD -6% from peak.
    """
    cfg = cfg or SimConfig()
    n = len(df)
    fit_end = int(n * fit_end_frac)
    fit = fit_markov(df.iloc[:fit_end])

    window = cfg.prop_window_hours
    oos_start = fit_end + 100
    oos_end = n - window - 1
    if oos_end <= oos_start:
        # fallback: allow starts in later half
        oos_start = max(n // 2, 500)
        oos_end = n - window - 1

    rng = np.random.default_rng(cfg.seed)
    starts = rng.choice(np.arange(oos_start, oos_end), size=cfg.n_prop_runs, replace=False)
    starts = np.sort(starts)

    runs = []
    breaches_total = {"daily_3pct": 0, "dd_6pct": 0, "leverage_5x": 0}
    monthly_profits = []

    for k, s in enumerate(starts):
        e = int(s + window)
        res = run_backtest(
            df,
            fit=fit,
            start_i=int(s),
            end_i=e,
            initial_equity=ACCOUNT_EQUITY,
            flatten_on_pass=True,
            prop_mode=True,
        )
        st = res.stats
        passed = bool(st["passed"]) and not bool(st["failed"])
        # monthly profit proxy: return over window scaled to 30d
        hours = max(len(res.equity_curve), 1)
        total_ret = st["total_return"]
        monthly = (1 + total_ret) ** (30 * 24 / hours) - 1 if hours > 0 else total_ret
        # if passed early, use actual return (~10%+)
        if passed:
            monthly = max(total_ret, monthly)
        monthly_profits.append(float(monthly))

        for b in breaches_total:
            breaches_total[b] += int(st["breaches"].get(b, 0))

        runs.append(
            {
                "run": k,
                "start_i": int(s),
                "start_ts": str(pd.Timestamp(df["timestamp"].iloc[int(s)])),
                "passed": passed,
                "failed": bool(st["failed"]),
                "fail_reason": st["fail_reason"],
                "final_equity": st["final_equity"],
                "total_return": st["total_return"],
                "monthly_profit": float(monthly),
                "n_trades": st["n_trades"],
                "max_dd": st["max_dd_observed"],
                "max_daily_loss": st["max_daily_loss_observed"],
                "max_leverage": st["max_leverage_used"],
            }
        )

    passes = sum(1 for r in runs if r["passed"])
    fails = cfg.n_prop_runs - passes

    # aggregate trade stats from a long OOS backtest for sharpe etc.
    oos_bt = run_backtest(df, fit=fit, start_i=fit_end, end_i=n, prop_mode=False)

    metrics = {
        "agent": "A",
        "strategy": "markov_regime",
        "prop_pass_rate": passes / cfg.n_prop_runs,
        "prop_passes": int(passes),
        "prop_fails": int(fails),
        "monthly_profit_mean": float(np.mean(monthly_profits)),
        "monthly_profit_median": float(np.median(monthly_profits)),
        "max_daily_loss_observed": float(
            min(r["max_daily_loss"] for r in runs) if runs else 0.0
        ),
        "max_dd_observed": float(max(r["max_dd"] for r in runs) if runs else 0.0),
        "max_leverage_used": float(max(r["max_leverage"] for r in runs) if runs else 0.0),
        "fees_bps": FEES_BPS,
        "slippage_bps": SLIPPAGE_BPS,
        "sharpe": float(oos_bt.stats["sharpe"]),
        "sortino": float(oos_bt.stats["sortino"]),
        "expectancy": float(oos_bt.stats["expectancy"]),
        "hitrate": float(oos_bt.stats["hitrate"]),
        "payoff_ratio": float(oos_bt.stats["payoff_ratio"]),
        "walk_forward_pass": None,  # filled by walk_forward
        "risk_breaches": breaches_total,
        "fit_end_frac": fit_end_frac,
        "n_runs": cfg.n_prop_runs,
        "prop_window_hours": window,
        "oos_backtest_monthly_mean": float(oos_bt.stats["monthly_profit_mean"]),
        "oos_backtest_monthly_median": float(oos_bt.stats["monthly_profit_median"]),
        "transition_matrix": fit.transition.tolist(),
        "edge_after_cost": {
            s: float(fit.edge_after_cost[i]) for i, s in enumerate(fit.states)
        },
        "emission_mean": {
            s: float(fit.emission_mean[i]) for i, s in enumerate(fit.states)
        },
        "runs": runs,
    }
    return metrics


def write_prop_report(metrics: dict, path: Path | None = None) -> Path:
    path = path or (REPORTS_DIR / "PROP_100_RUNS.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# PROP 100 RUNS — AGENT_A (Markov Regime)",
        "",
        "## Method",
        "1. Fit discrete Markov regime model (TREND_UP, TREND_DOWN, RANGE, SHOCK) on first 70% of hourly BTCUSD.",
        "2. Draw 100 unique randomized start indices uniformly from the OOS region.",
        "3. Each evaluation window ≈ 35 calendar days (`prop_window_hours = 24*35`).",
        "4. Account starts at $100,000. Pass = +$10,000 (+10%).",
        "5. Hard fail: daily loss ≤ -$3,000 OR drawdown from peak ≥ 6%, OR leverage attempt > 5x (clamped; counted).",
        "6. Fees 8 bps/side + slippage 3 bps/side applied on every entry/exit.",
        "7. Simulation stops early on pass or fail (`prop_mode=True`).",
        "",
        "## Summary",
        f"- Passes: **{metrics['prop_passes']}** / {metrics['n_runs']}",
        f"- Pass rate: **{metrics['prop_pass_rate']:.2%}**",
        f"- Fails: {metrics['prop_fails']}",
        f"- Monthly profit mean (window-scaled): {metrics['monthly_profit_mean']:.4%}",
        f"- Monthly profit median: {metrics['monthly_profit_median']:.4%}",
        f"- Max daily loss observed: {metrics['max_daily_loss_observed']:.4%}",
        f"- Max DD observed: {metrics['max_dd_observed']:.4%}",
        f"- Max leverage used: {metrics['max_leverage_used']:.3f}x",
        f"- Risk breaches: `{metrics['risk_breaches']}`",
        "",
        "## Edge per state (after costs)",
        "```",
        json.dumps(metrics.get("edge_after_cost", {}), indent=2),
        "```",
        "",
        "## Transition matrix P(s'|s)",
        "```",
        json.dumps(metrics.get("transition_matrix", []), indent=2),
        "```",
        "",
        "## Per-run table (abbreviated)",
        "",
        "| run | start | passed | fail_reason | ret | monthly | maxDD | maxDayLoss |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in metrics.get("runs", []):
        lines.append(
            f"| {r['run']} | {r['start_ts'][:10]} | {r['passed']} | {r['fail_reason']} | "
            f"{r['total_return']:.4f} | {r['monthly_profit']:.4f} | {r['max_dd']:.4f} | {r['max_daily_loss']:.4f} |"
        )
    path.write_text("\n".join(lines))
    return path
