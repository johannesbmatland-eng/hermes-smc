"""Configuration: Kraken-design costs, prop rules, A+ filter thresholds."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class CostModel:
    """Kraken-design retail taker + slippage (conservative)."""

    taker_fee_bps: float = 26.0  # 0.26% Kraken mid-tier taker
    slippage_bps: float = 5.0  # 0.05% adverse slippage per fill
    # Round-trip cost fraction of notional
    @property
    def round_trip_frac(self) -> float:
        one_way = (self.taker_fee_bps + self.slippage_bps) / 10_000.0
        return 2.0 * one_way

    @property
    def one_way_frac(self) -> float:
        return (self.taker_fee_bps + self.slippage_bps) / 10_000.0


@dataclass(frozen=True)
class PropRules:
    account_usd: float = 100_000.0
    pass_pct: float = 0.10  # +10%
    daily_loss_limit: float = 0.03  # -3%
    max_dd_hwm: float = 0.06  # -6% from peak
    max_leverage: float = 5.0


@dataclass
class StrategyParams:
    """A+ volatility/flow breakout parameters (STRICT)."""

    # --- Volatility breakout definition ---
    atr_len: int = 14
    atr_baseline_len: int = 48  # median ATR lookback (hours)
    atr_expand_min: float = 1.35  # ATR / medianATR must exceed

    # Range break
    range_lookback: int = 24  # hours (1D Donchian-ish)
    break_buffer_atr: float = 0.15  # must clear range by this * ATR

    # False-break filter
    confirm_bars: int = 2  # hold outside range for N bars
    false_break_reentry_atr: float = 0.25  # invalidate if reclaim inside by this

    # Flow proxy
    vol_sma_len: int = 48
    vol_surge_min: float = 1.60  # volume / SMA
    flow_lookback: int = 6  # signed volume sum window
    flow_z_min: float = 0.75  # |flow z-score| minimum aligned with break dir

    # Regime (trend vs chop)
    er_len: int = 24  # Kaufman efficiency ratio
    er_min: float = 0.28  # reject pure chop
    ema_fast: int = 48
    ema_slow: int = 168  # ~1W hourly
    require_ema_align: bool = True

    # Time-of-day / DOW filters (UTC hour)
    # Prefer London/NY overlap + post-US open flow; avoid thin Asia open chop
    allowed_hours_utc: tuple[int, ...] = (
        7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20
    )
    blocked_dow: tuple[int, ...] = ()  # 0=Mon ... 6=Sun; keep open but size smaller weekends via ER

    # Trade management
    stop_atr_mult: float = 1.35
    target_atr_mult: float = 3.20  # asymmetric payoff for expectancy
    time_stop_bars: int = 36  # exit if neither hit
    trail_atr_mult: float = 1.10  # optional trail after 1R
    use_trail: bool = True

    # Sizing / fee hurdle
    risk_frac_equity: float = 0.0075  # 0.75% equity risk per A+ trade
    max_leverage: float = 4.5  # under 5x hard cap
    min_edge_multiple_of_rt_cost: float = 2.5  # expectancy / RT cost hurdle
    # Prior estimate used for fee-hurdle gate (updated from IS research)
    prior_hit_rate: float = 0.42
    prior_avg_win_R: float = 1.85
    prior_avg_loss_R: float = 1.00

    # Cool-down after trade (bars) — enforces low frequency
    cooldown_bars: int = 12

    # Shock filter: skip if 1-bar return abs > shock (gap/liquidation chaos)
    shock_ret_abs: float = 0.035


DEFAULT_COSTS = CostModel()
DEFAULT_PROP = PropRules()
DEFAULT_PARAMS = StrategyParams()


def params_dict(p: StrategyParams | None = None) -> dict[str, Any]:
    return asdict(p or DEFAULT_PARAMS)
