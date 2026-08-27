# AGENT_C STATUS

**Role:** AGENT_C — The Macro Flow Analyst  
**Strategy lock:** Event / flow / volatility breakout (STRICT A+ filters)  
**Updated:** 2026-08-27T15:11:00Z  
**State:** IN_PROGRESS — Round 0 kickoff

## ACK
- Inbox `to_agent_c.md` Round-0 requirements received and locked.
- Scoreboard read: all agents AWAITING_SUBMISSION, score 0.
- Will NOT copy A (Markov) or B (session microstructure).

## Current iteration
1. Bootstrap dirs + ACK status
2. Acquire BTCUSD Kraken-design OHLC
3. Market study (7 sections)
4. Build A+ breakout engine + hard risk
5. Walk-forward + 100 prop sims
6. Deliver metrics.json / reports / README / COMPETITION_SCORE

## Notes
Default = no trade. Size only when expectancy > fee hurdle.
Target: low trade frequency, 10–15%/mo after fees/slippage, prop pass ≥90/100.
