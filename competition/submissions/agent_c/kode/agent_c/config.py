"""Configuration: Kraken-futures-design costs, prop rules, A+ breakout-pullback params."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class CostModel:
    """Kraken-design perpetual/futures-style taker + slippage."""

    taker_fee_bps: float = 5.0  # 0.05% Kraken Futures-like taker
    slippage_bps: float = 3.0  # 0.03% adverse slippage

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
    # Soft stops (flatten / halt trading before hard fail)
    soft_daily_loss: float = 0.018
    soft_dd_hwm: float = 0.045


@dataclass
class StrategyParams:
    """A+ macro-flow vol breakout → continuation (4H primary).

    Vol breakout definition:
      ATR ratio >= atr_expand_min AND close clears Donchian(lookback)
    Entry modes:
      - direct: A+ confirmed break bar
      - pullback: first EMA-retest continuation after break (false-break survivor)
    """

    timeframe: str = "4h"
    lookbacks: tuple[int, ...] = (12, 16, 20)

    # Volatility expansion
    atr_len: int = 14
    atr_baseline_len: int = 40
    atr_expand_min: float = 0.95

    # Range break
    break_buffer_atr: float = 0.0

    # False-break / conviction
    min_close_location: float = 0.55
    false_break_reentry_atr: float = 0.50
    pullback_window: int = 10
    structure_hold_atr: float = 0.85

    # Flow proxy
    vol_sma_len: int = 30
    vol_surge_min: float = 1.05

    # Regime
    er_len_mode: str = "lookback"  # ER length = active lookback
    er_min: float = 0.15
    ema_fast: int = 20
    ema_slow: int = 50
    ema_sep_min: float = 0.002
    require_ema_align: bool = True

    # Session
    blocked_dow: tuple[int, ...] = (5, 6)

    # Management — trail-only (fat right tail; proven +E on this family)
    stop_atr_mult: float = 1.25
    target_atr_mult: float = 99.0  # effectively disabled
    time_stop_bars: int = 16
    trail_atr_mult: float = 1.20
    use_trail: bool = True
    use_fixed_target: bool = False

    # Sizing / fee hurdle
    risk_frac_equity: float = 0.018
    max_leverage: float = 4.5
    min_edge_multiple_of_rt_cost: float = 1.0
    prior_hit_rate: float = 0.52
    prior_avg_win_R: float = 2.10
    prior_avg_loss_R: float = 1.05
    structural_win_capture: float = 1.0  # trail-only structural prior uses fitted R
    use_structural_fee_hurdle: bool = True

    # Dedup / cooldown across lookbacks
    dedup_hours: float = 4.0
    cooldown_bars: int = 2

    # Allow direct break entries in addition to pullbacks
    allow_direct_entry: bool = True
    allow_pullback_entry: bool = True

    shock_ret_abs: float = 0.08  # 4H shock gate


DEFAULT_COSTS = CostModel()
DEFAULT_PROP = PropRules()
DEFAULT_PARAMS = StrategyParams()


def params_dict(p: StrategyParams | None = None) -> dict[str, Any]:
    return asdict(p or DEFAULT_PARAMS)
