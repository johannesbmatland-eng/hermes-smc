#!/usr/bin/env python3
"""Entrypoint: research fit → walk-forward → 100 prop sims → write metrics."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# allow `python -m` or direct path execution
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))  # submissions/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from agent_a.kode.config import (  # noqa: E402
    FEES_BPS,
    REPORTS_DIR,
    ROUND_TRIP_COST_BPS,
    SLIPPAGE_BPS,
    STATES,
    SimConfig,
)
from agent_a.kode.data_loader import data_meta, load_ohlcv  # noqa: E402
from agent_a.kode.markov_model import classify_regimes, fit_markov, format_transition  # noqa: E402
from agent_a.kode.prop_sim import run_prop_100, write_prop_report  # noqa: E402
from agent_a.kode.walk_forward import walk_forward  # noqa: E402
from agent_a.kode.backtest import run_backtest  # noqa: E402


def research_summary(df: pd.DataFrame, fit) -> dict:
    labels = fit.label_series
    ret = df["ret"].fillna(0.0)
    # time of day
    tod = df.groupby("hour")["ret"].agg(["mean", "std", "count"])
    dow = df.groupby("dow")["ret"].agg(["mean", "std", "count"])
    # large moves
    large = df.loc[ret.abs() > ret.abs().quantile(0.99)]
    return {
        "n_bars": len(df),
        "date_start": str(df["timestamp"].iloc[0]),
        "date_end": str(df["timestamp"].iloc[-1]),
        "state_counts": {STATES[i]: int((labels == i).sum()) for i in range(len(STATES))},
        "tod_mean_ret": tod["mean"].to_dict(),
        "dow_mean_ret": dow["mean"].to_dict(),
        "large_move_hours": large["hour"].value_counts().head(8).to_dict(),
        "large_move_dow": large["dow"].value_counts().to_dict(),
        "transition": format_transition(fit).to_dict(),
        "edge_after_cost": {STATES[i]: float(fit.edge_after_cost[i]) for i in range(4)},
        "emission_mean": {STATES[i]: float(fit.emission_mean[i]) for i in range(4)},
    }


def write_metrics(metrics: dict, path: Path) -> None:
    slim = {k: v for k, v in metrics.items() if k not in ("runs", "transition_matrix")}
    # keep required schema keys
    out = {
        "agent": metrics["agent"],
        "strategy": metrics["strategy"],
        "prop_pass_rate": metrics["prop_pass_rate"],
        "prop_passes": metrics["prop_passes"],
        "prop_fails": metrics["prop_fails"],
        "monthly_profit_mean": metrics["monthly_profit_mean"],
        "monthly_profit_median": metrics["monthly_profit_median"],
        "max_daily_loss_observed": metrics["max_daily_loss_observed"],
        "max_dd_observed": metrics["max_dd_observed"],
        "max_leverage_used": metrics["max_leverage_used"],
        "fees_bps": metrics["fees_bps"],
        "slippage_bps": metrics["slippage_bps"],
        "sharpe": metrics["sharpe"],
        "sortino": metrics["sortino"],
        "expectancy": metrics["expectancy"],
        "hitrate": metrics["hitrate"],
        "payoff_ratio": metrics["payoff_ratio"],
        "walk_forward_pass": metrics["walk_forward_pass"],
        "risk_breaches": metrics["risk_breaches"],
        "edge_after_cost": metrics.get("edge_after_cost"),
        "oos_backtest_monthly_mean": metrics.get("oos_backtest_monthly_mean"),
        "data": data_meta(),
        "extras": {k: slim[k] for k in slim if k not in {
            "agent","strategy","prop_pass_rate","prop_passes","prop_fails",
            "monthly_profit_mean","monthly_profit_median","max_daily_loss_observed",
            "max_dd_observed","max_leverage_used","fees_bps","slippage_bps","sharpe",
            "sortino","expectancy","hitrate","payoff_ratio","walk_forward_pass",
            "risk_breaches","edge_after_cost","emission_mean","oos_backtest_monthly_mean",
            "oos_backtest_monthly_median","fit_end_frac","n_runs","prop_window_hours",
        }},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2))


def main() -> int:
    print("=== AGENT_A Markov Regime — run_all ===")
    print("Data:", data_meta())
    df = load_ohlcv()
    print(f"Loaded {len(df)} bars: {df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}")

    fit = fit_markov(df.iloc[: int(len(df) * 0.70)], cost_bps_roundtrip=ROUND_TRIP_COST_BPS)
    print("Transition matrix:")
    print(format_transition(fit))
    print("Edge after cost:", {STATES[i]: fit.edge_after_cost[i] for i in range(4)})

    print("Walk-forward...")
    wf = walk_forward(df)
    print("WF pass:", wf["walk_forward_pass"], "mean OOS monthly:", wf.get("mean_oos_monthly"))

    print("Prop 100 sims...")
    cfg = SimConfig()
    metrics = run_prop_100(df, cfg=cfg)
    metrics["walk_forward_pass"] = bool(wf["walk_forward_pass"])
    metrics["walk_forward"] = {k: v for k, v in wf.items() if k != "folds"}
    metrics["walk_forward_folds"] = wf.get("folds", [])

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    write_prop_report(metrics, REPORTS_DIR / "PROP_100_RUNS.md")
    # attach research blob
    metrics["research"] = research_summary(df, fit_markov(df, cost_bps_roundtrip=ROUND_TRIP_COST_BPS))
    write_metrics(metrics, REPORTS_DIR / "metrics.json")

    # full runs dump
    (REPORTS_DIR / "prop_runs_full.json").write_text(json.dumps(metrics["runs"], indent=2))
    (REPORTS_DIR / "walk_forward.json").write_text(json.dumps(wf, indent=2, default=str))

    print("=== RESULTS ===")
    print(f"pass_rate={metrics['prop_pass_rate']:.2%} ({metrics['prop_passes']}/{metrics['prop_passes']+metrics['prop_fails']})")
    print(f"monthly_mean={metrics['monthly_profit_mean']:.4%} median={metrics['monthly_profit_median']:.4%}")
    print(f"sharpe={metrics['sharpe']:.3f} sortino={metrics['sortino']:.3f}")
    print(f"breaches={metrics['risk_breaches']}")
    print(f"wf_pass={metrics['walk_forward_pass']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
