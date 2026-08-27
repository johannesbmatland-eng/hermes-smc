"""
AGENT_C — Adaptive Regime Breakout (ARB)
Kraken-design paper/backtest bot. NO live trading. NO Hermes.
Prop rules hard-enforced: daily loss 3%, max DD 6%, leverage <= 5x.
"""

from .config import Config
from .risk import PropRiskEngine
from .strategy import AdaptiveRegimeBreakout
from .backtest import Backtester
from .paper import PaperRunner

__all__ = [
    "Config",
    "PropRiskEngine",
    "AdaptiveRegimeBreakout",
    "Backtester",
    "PaperRunner",
]
__version__ = "1.0.0"
