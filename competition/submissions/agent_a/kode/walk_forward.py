"""Walk-forward / out-of-sample validation for Markov regime strategy."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .backtest import run_backtest
from .markov_model import fit_markov


def walk_forward(
    df: pd.DataFrame,
    n_folds: int = 5,
    train_frac: float = 0.6,
    min_oos_sharpe: float = -0.25,
    min_oos_monthly: float = -0.02,
) -> dict:
    """
    Expanding-window walk-forward:
    - Fold k trains on [0, train_end), tests on [train_end, test_end).
    - Pass if majority of OOS folds have non-collapsed expectancy and
      average OOS monthly return > min_oos_monthly and sharpe > min_oos_sharpe.
    """
    n = len(df)
    # reserve last 40% as successive OOS blocks
    oos_region = int(n * (1 - train_frac))
    block = oos_region // n_folds
    train_end0 = int(n * train_frac)

    folds = []
    for k in range(n_folds):
        train_end = train_end0 + k * block
        test_end = min(train_end + block, n)
        if test_end - train_end < 24 * 20:
            continue
        fit = fit_markov(df.iloc[:train_end])
        res = run_backtest(df, fit=fit, start_i=train_end, end_i=test_end, prop_mode=False)
        folds.append(
            {
                "fold": k,
                "train_end": train_end,
                "test_end": test_end,
                "sharpe": res.stats["sharpe"],
                "sortino": res.stats["sortino"],
                "monthly_mean": res.stats["monthly_profit_mean"],
                "expectancy": res.stats["expectancy"],
                "hitrate": res.stats["hitrate"],
                "max_dd": res.stats["max_dd"],
                "n_trades": res.stats["n_trades"],
                "edge": {s: float(fit.edge_after_cost[i]) for i, s in enumerate(fit.states)},
            }
        )

    if not folds:
        return {"walk_forward_pass": False, "folds": [], "reason": "no_folds"}

    sharpes = [f["sharpe"] for f in folds]
    monthlies = [f["monthly_mean"] for f in folds]
    expectancies = [f["expectancy"] for f in folds]
    positive_edge_folds = sum(1 for f in folds if f["expectancy"] > 0)

    wf_pass = (
        float(np.mean(sharpes)) > min_oos_sharpe
        and float(np.mean(monthlies)) > min_oos_monthly
        and positive_edge_folds >= max(1, n_folds // 2)
        and not any(f["max_dd"] > 0.25 for f in folds)
    )

    return {
        "walk_forward_pass": bool(wf_pass),
        "folds": folds,
        "mean_oos_sharpe": float(np.mean(sharpes)),
        "mean_oos_monthly": float(np.mean(monthlies)),
        "mean_oos_expectancy": float(np.mean(expectancies)),
        "positive_expectancy_folds": int(positive_edge_folds),
    }
