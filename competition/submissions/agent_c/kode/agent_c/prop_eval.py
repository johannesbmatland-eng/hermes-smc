"""Walk-forward evaluation and 100-run prop challenge Monte Carlo."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .backtest import run_backtest, BacktestResult
from .config import StrategyParams, CostModel, PropRules, DEFAULT_PARAMS, DEFAULT_COSTS, DEFAULT_PROP
from .data import load_ohlc


def walk_forward(
    df: pd.DataFrame,
    train_months: int = 6,
    test_months: int = 2,
    step_months: int = 2,
    params: StrategyParams | None = None,
    costs: CostModel | None = None,
    prop: PropRules | None = None,
) -> list[dict[str, Any]]:
    """Anchored rolling walk-forward on OOS folds."""
    params = params or DEFAULT_PARAMS
    costs = costs or DEFAULT_COSTS
    prop = prop or DEFAULT_PROP
    t0 = pd.Timestamp(df["timestamp"].iloc[0])
    t1 = pd.Timestamp(df["timestamp"].iloc[-1])
    folds = []
    cursor = t0 + pd.DateOffset(months=train_months)
    fold_id = 0
    while cursor + pd.DateOffset(months=test_months) <= t1 + pd.Timedelta(days=1):
        train_start = cursor - pd.DateOffset(months=train_months)
        test_start = cursor
        test_end = cursor + pd.DateOffset(months=test_months)
        train_df = df[(df["timestamp"] >= train_start) & (df["timestamp"] < test_start)].reset_index(drop=True)
        test_df = df[(df["timestamp"] >= test_start) & (df["timestamp"] < test_end)].reset_index(drop=True)
        if len(train_df) < 500 or len(test_df) < 200:
            cursor += pd.DateOffset(months=step_months)
            continue
        # Fit priors from train trades (update hit rate / R for fee hurdle)
        tr = run_backtest(train_df, params=params, costs=costs, prop=prop, enforce_prop_halt=False)
        fitted = _fit_priors(params, tr)
        te = run_backtest(test_df, params=fitted, costs=costs, prop=prop, enforce_prop_halt=True)
        folds.append(
            {
                "fold": fold_id,
                "train_start": str(train_start),
                "test_start": str(test_start),
                "test_end": str(test_end),
                "train_trades": tr.stats.get("n_trades", 0),
                "train_exp_R": tr.stats.get("expectancy_R", 0.0),
                "oos_stats": te.stats,
                "oos_pass_prop": te.passed_prop,
                "oos_daily_breach": te.daily_breach,
                "oos_dd_breach": te.dd_breach,
                "fitted_prior_hit_rate": fitted.prior_hit_rate,
            }
        )
        fold_id += 1
        cursor += pd.DateOffset(months=step_months)
    return folds


def _fit_priors(base: StrategyParams, result: BacktestResult) -> StrategyParams:
    """Update prior hit-rate / payoff from IS without changing structure."""
    trades = result.trades
    if len(trades) < 8:
        return base
    hits = sum(1 for t in trades if t.pnl > 0) / len(trades)
    win_R = [t.pnl_R for t in trades if t.pnl > 0]
    loss_R = [abs(t.pnl_R) for t in trades if t.pnl <= 0]
    avg_w = float(np.mean(win_R)) if win_R else base.prior_avg_win_R
    avg_l = float(np.mean(loss_R)) if loss_R else base.prior_avg_loss_R
    # blend toward base to avoid tiny-sample chaos
    hr = 0.6 * hits + 0.4 * base.prior_hit_rate
    # clamp sanity
    hr = float(np.clip(hr, 0.25, 0.65))
    avg_w = float(np.clip(0.6 * avg_w + 0.4 * base.prior_avg_win_R, 0.8, 3.5))
    avg_l = float(np.clip(0.6 * avg_l + 0.4 * base.prior_avg_loss_R, 0.7, 1.4))
    # copy via replace pattern
    d = asdict(base)
    d["prior_hit_rate"] = hr
    d["prior_avg_win_R"] = avg_w
    d["prior_avg_loss_R"] = avg_l
    return StrategyParams(**d)


def prop_challenge_window(
    df: pd.DataFrame,
    start_idx: int,
    max_bars: int,
    params: StrategyParams,
    costs: CostModel,
    prop: PropRules,
) -> dict[str, Any]:
    """Run one prop evaluation from a randomized start until pass/fail or window end.

    Challenge ends early on pass (+10%) or hard fail; otherwise judged at window end.
    Typical prop eval horizon ~30–60 calendar days of hourly bars.
    """
    end = min(len(df), start_idx + max_bars)
    window = df.iloc[start_idx:end].reset_index(drop=True)
    # Need warmup history for features — prepend lookback from before start
    warm = 400
    warm_start = max(0, start_idx - warm)
    full = df.iloc[warm_start:end].reset_index(drop=True)
    # Run on full but only score from challenge start equity path after warmup bars
    res = run_backtest(full, params=params, costs=costs, prop=prop, enforce_prop_halt=True)
    # Restrict equity curve to challenge period
    challenge_ts0 = df.iloc[start_idx]["timestamp"]
    eq = res.equity_curve[res.equity_curve["timestamp"] >= challenge_ts0].reset_index(drop=True)
    if eq.empty:
        return {
            "passed": False,
            "reason": "empty",
            "end_equity": prop.account_usd,
            "monthly_return": 0.0,
            "n_trades": 0,
            "daily_breach": False,
            "dd_breach": False,
            "leverage_breach": False,
            "max_dd": 0.0,
            "days": 0.0,
        }

    # Rebase: simulate starting at account size by scaling PnL path
    # Better: re-run with only window but features need history — use cash accounting from first challenge bar.
    # Approximate: take relative equity change from challenge start.
    e0 = float(eq["equity"].iloc[0])
    e1 = float(eq["equity"].iloc[-1])
    if e0 <= 0:
        rel = 0.0
    else:
        rel = e1 / e0
    end_eq = prop.account_usd * rel

    # Check breaches already from engine on full path — conservative.
    # Also compute max DD on challenge window rebased.
    path = prop.account_usd * (eq["equity"].to_numpy(dtype=np.float64) / e0)
    hwm = np.maximum.accumulate(path)
    dd = (hwm - path) / np.maximum(hwm, 1e-12)
    max_dd = float(np.max(dd))

    # Daily loss on rebased path
    eq2 = eq.copy()
    eq2["day"] = pd.to_datetime(eq2["timestamp"]).dt.date
    daily_breach = False
    for _, g in eq2.groupby("day"):
        day_path = prop.account_usd * (g["equity"].to_numpy(dtype=np.float64) / e0)
        day_start = day_path[0]
        day_min = float(np.min(day_path))
        if (day_min - day_start) / day_start <= -prop.daily_loss_limit:
            daily_breach = True
            break

    dd_breach = max_dd >= prop.max_dd_hwm
    lev_breach = res.leverage_breach

    t0 = pd.Timestamp(eq["timestamp"].iloc[0])
    t1 = pd.Timestamp(eq["timestamp"].iloc[-1])
    days = max((t1 - t0).total_seconds() / 86400.0, 1e-6)
    months = days / 30.4375
    monthly = (rel) ** (1.0 / max(months, 1e-6)) - 1.0 if rel > 0 else -1.0

    # Count trades in window
    n_tr = sum(1 for t in res.trades if t.entry_time >= challenge_ts0)

    passed = (
        (not daily_breach)
        and (not dd_breach)
        and (not lev_breach)
        and end_eq >= prop.account_usd * (1.0 + prop.pass_pct)
    )
    reason = "pass" if passed else (
        "daily" if daily_breach else "dd" if dd_breach else "lev" if lev_breach else "no_pass"
    )
    return {
        "passed": passed,
        "reason": reason,
        "end_equity": end_eq,
        "monthly_return": monthly,
        "total_return": rel - 1.0,
        "n_trades": n_tr,
        "daily_breach": daily_breach,
        "dd_breach": dd_breach,
        "leverage_breach": lev_breach,
        "max_dd": max_dd,
        "days": days,
        "start_time": str(challenge_ts0),
        "end_time": str(t1),
    }


def run_prop_100(
    df: pd.DataFrame,
    n_runs: int = 100,
    seed: int = 42,
    challenge_days: int = 45,
    params: StrategyParams | None = None,
    costs: CostModel | None = None,
    prop: PropRules | None = None,
) -> dict[str, Any]:
    params = params or DEFAULT_PARAMS
    costs = costs or DEFAULT_COSTS
    prop = prop or DEFAULT_PROP
    rng = np.random.default_rng(seed)
    bars = challenge_days * 24
    warm = 400
    max_start = len(df) - bars - 1
    if max_start <= warm:
        raise ValueError("insufficient data for prop windows")

    starts = rng.integers(warm, max_start, size=n_runs)
    runs = []
    for i, s in enumerate(starts):
        r = prop_challenge_window(df, int(s), bars, params, costs, prop)
        r["run"] = i
        r["start_idx"] = int(s)
        runs.append(r)

    passes = sum(1 for r in runs if r["passed"])
    daily_b = sum(1 for r in runs if r["daily_breach"])
    dd_b = sum(1 for r in runs if r["dd_breach"])
    lev_b = sum(1 for r in runs if r["leverage_breach"])
    mo = np.array([r["monthly_return"] for r in runs], dtype=np.float64)
    tr = np.array([r["n_trades"] for r in runs], dtype=np.float64)
    # trades per month from challenge window
    tpm = []
    for r in runs:
        m = max(r["days"] / 30.4375, 1e-6)
        tpm.append(r["n_trades"] / m)
    tpm_a = np.array(tpm, dtype=np.float64)

    summary = {
        "n_runs": n_runs,
        "pass_count": passes,
        "pass_rate": passes / n_runs,
        "monthly_return_mean": float(np.mean(mo)),
        "monthly_return_median": float(np.median(mo)),
        "monthly_return_p10": float(np.percentile(mo, 10)),
        "monthly_return_p90": float(np.percentile(mo, 90)),
        "trades_per_month_mean": float(np.mean(tpm_a)),
        "trades_per_month_median": float(np.median(tpm_a)),
        "daily_breach_count": daily_b,
        "dd_breach_count": dd_b,
        "leverage_breach_count": lev_b,
        "risk_ok": daily_b == 0 and dd_b == 0 and lev_b == 0,
        "challenge_days": challenge_days,
        "seed": seed,
        "runs": runs,
    }
    return summary


def full_sample_backtest(df: pd.DataFrame, params: StrategyParams | None = None) -> BacktestResult:
    return run_backtest(df, params=params or DEFAULT_PARAMS)
