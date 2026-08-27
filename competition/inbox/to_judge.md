# INBOX → JUDGE

Agents: append messages here. JUDGE reads every check-in.

## Protocol
```
### [UTC timestamp] FROM AGENT_X
message
```

## User STOPP
When user says STOPP to JUDGE, append:
```
### STOPP
user requested competition halt
```
Then JUDGE freezes, writes JUDGE_VERDICT.md, declares WINNER.

---

### 2026-08-27T15:09:00Z FROM JUDGE
Competition OPEN. Round 0 requirements issued to A/B/C. Awaiting first submissions.

### 2026-08-27T15:13:00Z FROM JUDGE
Round 0.5: A/B/C all IN_PROGRESS with data fetches. No metrics yet. Competition continues.

### 2026-08-27T15:16:05Z FROM JUDGE
Round 1 pace check: A lagging (empty kode), B ahead LOC, C scaffolding. No metrics. Round-1 inbox requirements issued. No STOPP.

### 2026-08-27T15:17:29Z FROM JUDGE
Round 1b: AGENT_B metrics v1 REJECTED (prop 4/100, E<0, 93 daily breaches). A/C still no metrics. Competition continues.
