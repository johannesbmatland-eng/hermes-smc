"""Paper-trading loop against Kraken public candles (no keys, no live orders)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .backtest import Backtester
from .config import Config
from .risk import PropRiskEngine


class PaperRunner:
    """
    Paper mode = run the same backtest engine on the latest public window,
    then optionally refresh periodically. Never places live orders.
    """

    def __init__(self, config: Config | None = None, out_dir: Path | None = None):
        self.cfg = config or Config()
        self.out_dir = out_dir or Path(__file__).resolve().parent / "results"
        self.risk = PropRiskEngine(self.cfg.prop)

    def run_once(self, limit: int = 720) -> dict[str, Any]:
        bt = Backtester(self.cfg)
        result = bt.run(limit=limit, allow_synthetic=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        bt.write_reports(result, self.out_dir)
        paper = {
            "mode": "paper",
            "exchange": "kraken_public_ohlcv",
            "live_orders": False,
            "hermes": False,
            "agent": self.cfg.agent_id,
            "strategy": self.cfg.strategy_name,
            "markets": list(self.cfg.strategy.markets),
            "metrics": result.metrics,
            "sources": result.source,
            "generated_at": time.time(),
        }
        (self.out_dir / "paper_report.json").write_text(json.dumps(paper, indent=2))
        return paper
