"""Competition + strategy configuration for AGENT_C."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PropRules:
    starting_capital: float = 100_000.0
    profit_target_pct: float = 10.0  # +$10,000
    daily_loss_pct: float = 3.0  # -$3,000 from day-start equity
    max_drawdown_pct: float = 6.0  # -$6,000 from peak
    max_leverage: float = 5.0


@dataclass
class CostModel:
    # Blended Kraken fee assumption (limit-biased) + small slippage
    fee_bps: float = 16.0
    slippage_bps: float = 4.0


@dataclass
class StrategyParams:
    """Tuned on Kraken BTC/USD 1h public OHLCV (grid search, fees+slip included)."""

    markets: tuple[str, ...] = ("BTC/USD",)
    timeframe: str = "1h"
    lookback: int = 36
    atr_period: int = 14
    ema_fast: int = 20
    ema_slow: int = 50
    regime_vol_lookback: int = 72
    chop_vol_percentile: float = 35.0
    chaos_vol_percentile: float = 92.0
    min_atr_pct: float = 0.20
    risk_pct_per_trade: float = 0.35
    rr_target: float = 2.2
    sl_atr_mult: float = 2.0
    max_open_positions: int = 1
    cooldown_bars: int = 3
    regime_persist_bars: int = 2
    breakout_buffer_atr: float = 0.15
    min_ema_sep_pct: float = 0.15


@dataclass
class Config:
    prop: PropRules = field(default_factory=PropRules)
    costs: CostModel = field(default_factory=CostModel)
    strategy: StrategyParams = field(default_factory=StrategyParams)
    exchange_id: str = "kraken"
    agent_id: str = "AGENT_C"
    strategy_name: str = "Adaptive Regime Breakout (ARB)"
