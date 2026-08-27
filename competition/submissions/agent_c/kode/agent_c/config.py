"""Configuration: Kraken-design costs, prop rules, A+ filter thresholds."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class CostModel:
    """Kraken-design retail taker + slippage (conservative but realistic)."""

    taker_fee_bps: float = 16.0  # Kraken BTCUSD mid-volume taker ~0.16%
    slippage_bps: float = 4.0  # 0.04% adverse slippage per fill

    @property
    def round_trip_frac(self) -> float:
        return 2.0 * self.one_way_frac

    @property
    def one_way_frac(self) -> float:
        return (self.taker_fee_bps + self.slippage_bps) / 10_000.0


@dataclass(frozen=True)
class PropRules:
    account_usd: float = 100_000.0
    pass_pct: float = 0.10
    daily_loss_limit: float = 0.03
    max_dd_hwm: float = 0.06
    max_leverage: float = 5.0


@dataclass
class StrategyParams:
    """A+ volatility/flow breakout parameters (STRICT, default=no-trade)."""

    # Volatility breakout
    atr_len: int = 14
    atr_baseline_len: int = 48
    atr_expand_min: float = 1.25

    # Range break (Donchian)
    range_lookback: int = 24
    break_buffer_atr: float = 0.10

    # False-break filter
    confirm_bars: int = 1  # break bar close must hold outside
    false_break_reentry_atr: float = 0.35  # wick cannot reclaim this deep
    min_close_location: float = 0.55  # close in upper/lower portion of bar

    # Flow proxy
    vol_sma_len: int = 48
    vol_surge_min: float = 1.40
    flow_lookback: int = 6
    flow_z_min: float = 0.55

    # Regime
    er_len: int = 24
    er_min: float = 0.22
    ema_fast: int = 48
    ema_slow: int = 168
    require_ema_align: bool = True

    # Session (UTC) — London through NY
    allowed_hours_utc: tuple[int, ...] = (
        7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20
    )
    blocked_dow: tuple[int, ...] = (5, 6)  # skip weekend thin flow

    # Trade management — asymmetric payoff for fee-clearing expectancy
    stop_atr_mult: float = 1.25
    target_atr_mult: float = 3.50
    time_stop_bars: int = 48
    trail_atr_mult: float = 1.20
    use_trail: bool = True

    # Sizing / fee hurdle
    risk_frac_equity: float = 0.012  # 1.2% equity risk per A+ trade
    max_leverage: float = 4.5
    min_edge_multiple_of_rt_cost: float = 1.15
    # Structural prior (updated from IS)
    prior_hit_rate: float = 0.40
    prior_avg_win_R: float = 2.20
    prior_avg_loss_R: float = 1.00
    use_structural_fee_hurdle: bool = True
    structural_win_capture: float = 0.70  # fraction of target_R realized on wins

    cooldown_bars: int = 18
    shock_ret_abs: float = 0.045


DEFAULT_COSTS = CostModel()
DEFAULT_PROP = PropRules()
DEFAULT_PARAMS = StrategyParams()


def params_dict(p: StrategyParams | None = None) -> dict[str, Any]:
    return asdict(p or DEFAULT_PARAMS)
