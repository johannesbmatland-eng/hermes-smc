# hermes-smc
ICT/SMC trading bot for BTC/USD. Strategy:
- 5m chart main, with 1h/15m trend filter
- Unmitigated FVGs, pullback + confirmation entry
- Longs in uptrend (bullish FVG), shorts in downtrend (bearish FVG)
- 0.5% risk per trade, RR 1/2 to 1/3
- Paper trading with 100k USD starting capital
- Dashboard: live BTC/USD chart, EMA/FVG status, and what the bot is waiting for

## Structure
- engine/: core logic (data, detection, execution)
- config/: strategy config YAML
- dashboard/: web dashboard
- state_defaults/: default state files
