# hermes-smc
ICT/SMC trading bot for BTC/EUR. Strategy:
- 5m chart main, with 1h/15m trend filter
- Unmitigated FVGs, pullback + confirmation entry
- 0.5% risk per trade, RR 1/2 to 1/3
- Paper trading with 100k USD starting capital

## Structure
- engine/: core logic (data, detection, execution)
- config/: strategy config YAML
- dashboard/: web dashboard
- state_defaults/: default state files
