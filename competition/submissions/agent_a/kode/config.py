"""Agent A — Markov Regime Prop Bot configuration (Kraken-design BTCUSD)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
KODE_DIR = ROOT / "kode"

ACCOUNT_EQUITY = 100_000.0
PASS_PROFIT = 10_000.0
DAILY_LOSS_LIMIT = 3_000.0
MAX_DD_FROM_PEAK = 0.06
MAX_LEVERAGE = 5.0

# Kraken-design costs
FEES_BPS = 8.0
SLIPPAGE_BPS = 3.0
COST_BPS_PER_SIDE = FEES_BPS + SLIPPAGE_BPS
ROUND_TRIP_COST_BPS = 2 * COST_BPS_PER_SIDE

STATES = ("TREND_UP", "TREND_DOWN", "RANGE", "SHOCK")
STATE_IDX = {s: i for i, s in enumerate(STATES)}

TREND_LOOKBACK = 24
VOL_LOOKBACK = 48
SHOCK_VOL_Z = 2.0
SHOCK_BAR_RET = 0.018
SHOCK_CUM3 = 0.028
TREND_ABS_RET = 0.012
RANGE_ABS_RET = 0.006

# Strategy — trade SHOCK recovery & select transitions only
MIN_POSTERIOR = 0.45
EDGE_FLOOR_AFTER_COST = 0.0005
BASE_NOTIONAL_LEV = 2.75  # base leverage when edge fires
HIGH_EDGE_LEV = 3.75
MAX_HOLD_BARS = 36
RECOVERY_HOLD = 36
DAILY_SOFT_STOP_FRAC = 0.017
DD_SOFT_STOP_FRAC = 0.038
# Prefer US/EU active hours for shock entries
PREFERRED_HOURS = set(range(12, 21))

DATA_PATH = DATA_DIR / "btcusd_hourly_coinbase.csv"
DATA_SOURCE_NOTE = (
    "Coinbase Exchange public BTC-USD hourly candles 2020-01-01 → 2026-08-27 "
    "(BTCUSD proxy; Kraken-design fees/slippage). Kraken OHLC capped ~720 bars."
)


@dataclass
class SimConfig:
    account_equity: float = ACCOUNT_EQUITY
    pass_profit: float = PASS_PROFIT
    daily_loss_limit: float = DAILY_LOSS_LIMIT
    max_dd_from_peak: float = MAX_DD_FROM_PEAK
    max_leverage: float = MAX_LEVERAGE
    fees_bps: float = FEES_BPS
    slippage_bps: float = SLIPPAGE_BPS
    seed: int = 42
    prop_window_hours: int = 24 * 40
    n_prop_runs: int = 100
