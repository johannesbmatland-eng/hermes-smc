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
