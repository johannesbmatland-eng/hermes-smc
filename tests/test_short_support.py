"""Unit tests for long/short SMC entry mirroring."""

from hermes_smc.engine.smc_engine import PositionManager, SMCConfig, SMCEngine
from hermes_smc.engine.paper_trading import PaperTradingEngine
from hermes_smc.engine.core import MarketStructureDetector, TrendAnalyzer


def _candle(ts, o, h, l, c, v=1.0):
    return {"timestamp": ts, "open": o, "high": h, "low": l, "close": c, "volume": v}


def test_position_manager_sl_tp_long_vs_short():
    pm = PositionManager()
    entry = 100.0
    fvg_bottom = 98.0
    fvg_top = 102.0

    sl_long = pm.calculate_sl_price(entry, fvg_bottom, side="long", fvg_top=fvg_top)
    tp_long = pm.calculate_tp_price(entry, sl_long, rr_target=0.5, side="long")
    assert sl_long < fvg_bottom
    assert tp_long > entry

    sl_short = pm.calculate_sl_price(entry, fvg_bottom, side="short", fvg_top=fvg_top)
    tp_short = pm.calculate_tp_price(entry, sl_short, rr_target=0.5, side="short")
    assert sl_short > fvg_top
    assert tp_short < entry


def test_short_pnl_and_capital():
    pm = PositionManager(initial_capital=100_000)
    pm.open_position(
        trade_id="s1",
        asset="BTC/USD",
        side="short",
        entry_price=100.0,
        position_size=10.0,
        sl_price=105.0,
        tp_price=90.0,
        strategy_info={},
    )
    assert pm.capital == 100_000 - 1000.0

    closed = pm.close_position("s1", exit_price=90.0, exit_reason="take_profit")
    assert closed is not None
    assert closed["pnl"] == 100.0  # (100-90)*10
    assert abs(pm.capital - (100_000 + 100.0)) < 1e-6


def test_detect_bearish_fvg():
    # Gap down: prev low above current high
    candles = [
        _candle(1, 100, 101, 99, 100),
        _candle(2, 100, 100.5, 98, 99),
        _candle(3, 95, 96, 94, 95),  # high 96 < prev low 98 → bearish FVG
    ]
    fvgs = MarketStructureDetector.detect_fvg(candles)
    bearish = [f for f in fvgs if f["type"] == "bearish"]
    assert len(bearish) >= 1


def test_paper_engine_short_sl_tp_helpers():
    engine = PaperTradingEngine(SMCConfig())
    fvg = {"top": 105.0, "bottom": 100.0, "mid": 102.5, "type": "bearish"}
    entry = 102.0

    sl = engine._calculate_smc_sl(entry, fvg, side="short")
    tp = engine._calculate_smc_tp(entry, sl, side="short")
    size = engine._calculate_position_size(entry, sl)

    assert sl > fvg["top"]
    assert tp < entry
    assert size > 0


def test_bearish_engulfing_confirmation():
    engine = PaperTradingEngine(SMCConfig())
    candles = [
        _candle(1, 100, 101, 99, 100),
        _candle(2, 100, 103, 99.5, 102.5),  # bullish prev
        _candle(3, 103, 103.2, 98, 98.5),   # bearish engulfing
    ]
    fvg = {"top": 103.0, "bottom": 99.0, "type": "bearish"}
    conf = engine._check_smc_confirmation(candles, None, fvg, side="short")
    assert conf == "engulfing_5m"


def test_exit_conditions_short():
    engine = SMCEngine(SMCConfig())
    position = {
        "entry_price": 100.0,
        "stop_loss": 105.0,
        "take_profit": 90.0,
        "side": "short",
    }
    candles = [_candle(i, 100, 101, 99, 100) for i in range(12)]

    assert engine.check_exit_conditions(position, candles, 106.0) == "stop_loss"
    assert engine.check_exit_conditions(position, candles, 89.0) == "take_profit"


def test_analysis_snapshot_has_checklist_and_phase():
    engine = PaperTradingEngine(SMCConfig())
    # Synthetic rising market with enough candles
    candles = []
    price = 100.0
    for i in range(120):
        o = price
        c = price + 0.2
        candles.append(_candle(1_700_000_000 + i * 300, o, c + 0.1, o - 0.1, c))
        price = c

    snap = engine.build_analysis_snapshot(candles, candles, candles, candles[-30:], price)
    assert snap["market"] == "BTC/USD"
    assert "phase" in snap
    assert isinstance(snap["checklist"], list)
    assert len(snap["checklist"]) >= 3
    assert "ema" in snap
    assert "fvgs" in snap


def test_ema_series_length():
    engine = PaperTradingEngine(SMCConfig())
    candles = [_candle(1_700_000_000 + i * 300, 100, 101, 99, 100 + i * 0.01) for i in range(80)]
    series = engine._ema_series(candles, 50)
    assert len(series) == 80 - 50 + 1
    assert "time" in series[0] and "value" in series[0]
