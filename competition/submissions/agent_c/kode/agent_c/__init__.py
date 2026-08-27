"""AGENT_C — Macro Flow / Volatility Breakout (STRICT A+ filters).

Default = no trade. Size only when expectancy clears fee/slippage hurdle.
Kraken-design BTCUSD fees + slippage included. Prop hard fails enforced.
"""

from __future__ import annotations

__version__ = "0.1.0"
__agent__ = "C"
__strategy__ = "macro_flow_breakout"
