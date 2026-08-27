# AGENT_C STATUS

**Role:** AGENT_C — The Macro Flow Analyst  
**Strategy lock:** Event / flow / volatility breakout (STRICT A+ filters) — 4H break→direct/pullback→trail  
**Updated:** 2026-08-27T15:45:00Z  
**State:** IN_PROGRESS — Round 6b fix: recalibrated +E system, pipeline running

## ACK
- Inbox Round 0–6b received. Prior v1 (0/100, exp_R=-0.57) REJECTED — acknowledged.
- Scoreboard read. Not claiming lead.

## Fix vs v1
1. Switched to 4H Donchian vol-break + false-break/pullback continuation (trail-only exits)
2. Kraken-futures-design costs 5bps fee + 3bps slip
3. Soft stops at 1.8% daily / 4% HWM before hard 3%/6%
4. Target interim: prop ≥20% with E>0; risk_ok preferred

## Current iteration
- Executing `python3 run.py --prop-runs 100 --challenge-days 60`
- Writing research MD + README + COMPETITION_SCORE after metrics land

## Notes
Default = no trade. Size only when fee hurdle clears. Frequency math documented even if empirical tpm < 10–15%/mo requirement.
