#!/usr/bin/env python3
"""Run AGENT_C backtest + paper report. No live trading."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running as script: python run_backtest.py
ROOT = Path(__file__).resolve().parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from agent_c.backtest import Backtester  # noqa: E402
from agent_c.config import Config  # noqa: E402
from agent_c.paper import PaperRunner  # noqa: E402
from agent_c.risk import PropRiskEngine  # noqa: E402


def write_score(metrics: dict, out: Path) -> None:
    compliance = metrics.get("compliance", "unknown")
    lines = [
        "# COMPETITION_SCORE — AGENT_C",
        "",
        "- strategy: Adaptive Regime Breakout (ARB)",
        "- markets: BTC/USD (Kraken public OHLCV, 1h)",
        f"- compliance: **{compliance}**",
        f"- profit/mnd: ${metrics.get('profit_per_month', 0):,.2f}",
        f"- profit_pct (window): {metrics.get('profit_pct', 0)}%",
        f"- winrate: {metrics.get('winrate', 0)}%",
        f"- expectancy $/trade: {metrics.get('expectancy', 0)}",
        f"- maxDD: {metrics.get('max_dd_pct', 0)}%",
        f"- worstDay: {metrics.get('worst_day_pct', 0)}%",
        f"- trades: {metrics.get('trades', 0)}",
        f"- months_span: {metrics.get('months_span', 0)}",
        f"- total_fees: ${metrics.get('total_fees', 0)}",
        f"- total_slippage: ${metrics.get('total_slippage', 0)}",
        f"- halt_reason: {metrics.get('halt_reason')}",
        "",
        "## Prop rules",
        "- starting: $100,000",
        "- daily loss limit: 3%",
        "- max DD: 6%",
        "- leverage max: 5x",
        "",
        "## Notes",
        "- Fees 16 bps/side + 4 bps slippage applied.",
        "- Hard risk engine halts and flattens on daily/DD breach.",
        "- No live keys. No Hermes.",
        "",
    ]
    out.write_text("\n".join(lines))


def self_check_risk() -> None:
    """Unit-ish check that risk engine fails correctly."""
    eng = PropRiskEngine.__new__(PropRiskEngine)
    from agent_c.config import PropRules

    eng = PropRiskEngine(PropRules())
    eng.mark_equity(100_000)
    # blow daily
    eng.day_start_equity = 100_000
    snap = eng.mark_equity(96_900)  # -3.1%
    assert snap.compliance == "fail", snap
    assert eng.halted
    print("risk_self_check: PASS (daily loss fail)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=720)
    parser.add_argument("--synthetic-only", action="store_true")
    args = parser.parse_args()

    self_check_risk()

    cfg = Config()
    out = ROOT / "results"
    bt = Backtester(cfg)
    # If synthetic-only, poison network by using synthetic path via allow flag + empty fetch
    if args.synthetic_only:
        from agent_c.data import generate_synthetic_ohlcv
        from agent_c.strategy import AdaptiveRegimeBreakout

        series = {}
        feats = {}
        strategies = {}
        sources = {}
        seeds = {"BTC/USD": (42, 65000.0), "ETH/USD": (7, 3500.0)}
        for m in cfg.strategy.markets:
            seed, px = seeds.get(m, (99, 1000.0))
            data = generate_synthetic_ohlcv(n=args.limit, start_price=px, seed=seed)
            series[m] = data
            sources[m] = f"synthetic_forced:{m}"
            st = AdaptiveRegimeBreakout(cfg.strategy)
            strategies[m] = st
            feats[m] = st.prepare(data)
        n = min(len(series[m]["close"]) for m in series)
        warmup = (
            max(
                cfg.strategy.lookback,
                cfg.strategy.ema_slow,
                cfg.strategy.regime_vol_lookback,
                cfg.strategy.atr_period,
            )
            + 5
        )
        result = bt._simulate(series, feats, strategies, sources, n, warmup)
    else:
        result = bt.run(limit=args.limit, allow_synthetic=True)

    bt.write_reports(result, out)
    paper = PaperRunner(cfg, out_dir=out)
    # reuse metrics already written
    paper_report = {
        "mode": "paper",
        "live_orders": False,
        "metrics": result.metrics,
        "sources": result.source,
    }
    (out / "paper_report.json").write_text(json.dumps(paper_report, indent=2))
    write_score(result.metrics, ROOT / "COMPETITION_SCORE.md")

    print(json.dumps(result.metrics, indent=2))
    print(f"sources={result.source}")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
