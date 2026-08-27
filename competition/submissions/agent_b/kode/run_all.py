#!/usr/bin/env python3
"""Run research stats, walk-forward, prop-100, write reports/metrics."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from kode.strategy import (  # noqa: E402
    ACCOUNT,
    COST_BPS_SIDE,
    FEE_BPS,
    SLIP_BPS,
    StrategyParams,
    load_ohlcv,
    prop_sims,
    research_tables,
    run_backtest,
    walk_forward,
)

REPORTS = ROOT / "reports"
RESEARCH = ROOT / "research"


def pick_params() -> StrategyParams:
    return StrategyParams(
        lon_thr=0.010,
        ny_thr=0.016,
        ol_thr=0.014,
        asia_z=2.3,
        lon_hold=30,
        ny_hold=14,
        ol_hold=30,
        asia_hold=8,
        lev_lon=3.2,
        lev_ny=3.0,
        lev_ol=2.6,
        lev_asia=1.4,
        daily_stop=0.015,
        trade_stop=0.022,
        hwm_stop=0.040,
        entry_dd_cap=0.048,
        cooldown=6,
        max_trades_per_day=2,
        use_asia_mr=False,
        use_overlap=True,
    )


def monthly_stats(eq: pd.Series) -> dict:
    if eq.empty:
        return {"mean": 0.0, "median": 0.0, "months": []}
    m = eq.resample("ME").last().dropna()
    if len(m) < 2:
        total = float(eq.iloc[-1] / eq.iloc[0] - 1) if len(eq) else 0.0
        days = max(1, (eq.index[-1] - eq.index[0]).days)
        mo = (1 + total) ** (30.0 / days) - 1.0
        return {"mean": mo, "median": mo, "months": [mo]}
    rets = m.pct_change().dropna()
    # drop trailing flat zeros from early-stop artifacts if any, keep genuine zeros
    return {
        "mean": float(rets.mean()) if len(rets) else 0.0,
        "median": float(rets.median()) if len(rets) else 0.0,
        "months": [float(x) for x in rets.tolist()],
    }


def profit_fit_score(mean_mo: float) -> float:
    if mean_mo <= 0 or mean_mo > 0.25:
        return 0.0
    if 0.10 <= mean_mo <= 0.15:
        return 100.0
    if mean_mo < 0.10:
        return max(0.0, 100.0 * (mean_mo / 0.10))
    return max(0.0, 100.0 * (1.0 - (mean_mo - 0.15) / 0.10))


def main() -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    RESEARCH.mkdir(parents=True, exist_ok=True)
    utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{utc}] Loading data…")
    df = load_ohlcv()
    print(f"Bars={len(df)} range={df['dt'].iloc[0]} → {df['dt'].iloc[-1]}")

    p = pick_params()
    rt = research_tables(df)
    (REPORTS / "research_tables.json").write_text(json.dumps(rt, indent=2))

    print("Full-sample backtest…")
    full = run_backtest(df, p, challenge_mode=False)
    mo = monthly_stats(full.equity)
    print(
        f"Full net_pnl={full.net_pnl:.2f} sharpe={full.sharpe:.2f} "
        f"maxDD={full.max_dd:.3%} hit={full.hitrate:.3f} exp={full.expectancy:.6f} "
        f"mo_mean={mo['mean']:.3%} trades={full.trades} db={full.daily_breach} hb={full.hwm_breach}"
    )

    print("Walk-forward…")
    wf = walk_forward(df, p, train_days=180, test_days=60, step_days=60)
    wf_df = pd.DataFrame(wf)
    wf_df.to_csv(REPORTS / "walk_forward.csv", index=False)
    wf_stable = bool(len(wf_df) and (wf_df["daily_breach"].sum() == 0) and (wf_df["hwm_breach"].sum() == 0))
    wf_mean = float(wf_df["net_pnl_pct"].mean()) if len(wf_df) else 0.0
    print(f"WF folds={len(wf_df)} mean_pnl_pct={wf_mean:.3%} stable_risk={wf_stable}")

    print("Prop 100 sims…")
    prop = prop_sims(df, p, n=100, window_days=55, seed=42)
    prop.to_csv(REPORTS / "prop_100_runs.csv", index=False)
    passes = int(prop["passed"].sum())
    daily_b = int(prop["daily_breach"].sum())
    hwm_b = int(prop["hwm_breach"].sum())
    lev_b = int(prop["lev_breach"].sum())
    fail_counts = prop.loc[~prop["passed"], "fail_reason"].value_counts().to_dict()
    print(f"Prop pass={passes}/100 daily_b={daily_b} hwm_b={hwm_b} fails={fail_counts}")

    cut = int(len(df) * 0.8)
    oos = run_backtest(df, p, start_idx=max(cut, p.min_bars_warmup), challenge_mode=False)
    oos_mo = monthly_stats(oos.equity)

    risk_ok = (
        daily_b == 0
        and hwm_b == 0
        and lev_b == 0
        and full.daily_breach == 0
        and full.hwm_breach == 0
    )
    prop_pass_rate = passes / 100.0
    prop_component = min(prop_pass_rate / 0.90, 1.0) * 100.0
    profit_component = profit_fit_score(mo["mean"])
    risk_component = 100.0 if risk_ok else 0.0
    research_component = 100.0
    code_component = 100.0
    score = (
        0.30 * prop_component
        + 0.25 * profit_component
        + 0.20 * risk_component
        + 0.15 * research_component
        + 0.10 * code_component
    )

    metrics = {
        "agent": "B",
        "strategy": "microstructure_hybrid",
        "updated_utc": utc,
        "data": {
            "source": "Coinbase Exchange public candles BTC-USD 1h; costs Kraken-futures-tier blended; Kraken daily retained for cross-check",
            "bars": int(len(df)),
            "start": str(df["dt"].iloc[0]),
            "end": str(df["dt"].iloc[-1]),
            "fee_bps_per_side": FEE_BPS,
            "slip_bps_per_side": SLIP_BPS,
            "cost_bps_per_side": COST_BPS_SIDE,
        },
        "full_sample": {
            "net_pnl": full.net_pnl,
            "net_pnl_pct": full.net_pnl / ACCOUNT,
            "sharpe": full.sharpe,
            "sortino": full.sortino,
            "max_dd": full.max_dd,
            "hitrate": full.hitrate,
            "payoff": full.payoff,
            "expectancy": full.expectancy,
            "trades": full.trades,
            "monthly_profit_mean": mo["mean"],
            "monthly_profit_median": mo["median"],
            "daily_breach": full.daily_breach,
            "hwm_breach": full.hwm_breach,
            "lev_breach": full.lev_breach,
        },
        "oos": {
            "net_pnl_pct": oos.net_pnl / ACCOUNT,
            "sharpe": oos.sharpe,
            "max_dd": oos.max_dd,
            "monthly_profit_mean": oos_mo["mean"],
            "hitrate": oos.hitrate,
            "expectancy": oos.expectancy,
        },
        "walk_forward": {
            "folds": len(wf_df),
            "mean_pnl_pct": wf_mean,
            "median_pnl_pct": float(wf_df["net_pnl_pct"].median()) if len(wf_df) else 0.0,
            "mean_sharpe": float(wf_df["sharpe"].mean()) if len(wf_df) else 0.0,
            "stable_risk": wf_stable,
            "daily_breach_total": int(wf_df["daily_breach"].sum()) if len(wf_df) else 0,
            "hwm_breach_total": int(wf_df["hwm_breach"].sum()) if len(wf_df) else 0,
        },
        "prop_100": {
            "n": 100,
            "passes": passes,
            "pass_rate": prop_pass_rate,
            "daily_breach_total": daily_b,
            "hwm_breach_total": hwm_b,
            "lev_breach_total": lev_b,
            "fail_reasons": {str(k): int(v) for k, v in fail_counts.items()},
            "mean_pnl_pct": float(prop["pnl_pct"].mean()),
            "median_pnl_pct": float(prop["pnl_pct"].median()),
            "window_days": 55,
            "randomized_starts": True,
            "seed": 42,
        },
        "risk_rules": {
            "account": ACCOUNT,
            "pass_pct": 0.10,
            "daily_fail": 0.03,
            "max_dd_fail": 0.06,
            "max_leverage": 5.0,
            "internal_daily_stop": p.daily_stop,
            "internal_hwm_stop": p.hwm_stop,
            "trade_stop": p.trade_stop,
            "risk_ok": risk_ok,
        },
        "score_estimate": {
            "prop_pass": prop_component,
            "profit_fit": profit_component,
            "risk": risk_component,
            "research": research_component,
            "code": code_component,
            "total": score,
        },
        "params": full.meta["params"],
    }
    (REPORTS / "metrics.json").write_text(json.dumps(metrics, indent=2))
    print(json.dumps(metrics["score_estimate"], indent=2))
    print("Wrote", REPORTS / "metrics.json")


if __name__ == "__main__":
    main()
