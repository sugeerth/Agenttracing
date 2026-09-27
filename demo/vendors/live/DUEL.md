# haiku vs sonnet — same task, side by side

1 task, 2 paired runs, on equal terms throughout the parity ledger. haiku passed 2 of 2; sonnet passed 2 of 2. 1 of 2 pairs spent within ±10% of each other's tokens; where they did not, compare outcomes per token rather than per run. Passing runs per million tokens: haiku 10.232, sonnet 8.906. haiku explored 3 time(s) before its first edit (median) and ran a check after its last edit in 2 of 2 run(s); sonnet explored 3 time(s) before its first edit (median) and ran a check after its last edit in 2 of 2 run(s).

## Parity: what was equal

| condition | equal | haiku | sonnet |
|---|---|---|---|
| the prompt | yes | 9749235c6b465313 | 9749235c6b465313 |
| the starting workspace | yes | 4e286917e0eba120 | 4e286917e0eba120 |
| the check that grades it | yes | python3 -m unittest -q | python3 -m unittest -q |
| the token budget | yes | 500000 | 500000 |
| the time limit | yes | 600 | 600 |
| how the budget is enforced | yes | while running (usage is per message) | while running (usage is per message) |
| the sandbox | yes | claude acceptEdits (Bash allowed), no OS sandbox | claude acceptEdits (Bash allowed), no OS sandbox |
| the operator's own vendor settings | yes | ignored | ignored |
| the tools on offer | yes | Bash, Read, Edit, Write, Grep, Glob, TodoWrite, Task; no MCP servers | Bash, Read, Edit, Write, Grep, Glob, TodoWrite, Task; no MCP servers |
| launched side by side | yes | 0.0s |  |
| cost reporting | yes | reported by the CLI | reported by the CLI |
| the models | — | claude-haiku-4-5-20251001 | claude-sonnet-5 |

## Outcome and spend

| | haiku | sonnet |
|---|---|---|
| passed the check | 2 of 2 (34%–100%) | 2 of 2 (34%–100%) |
| median tokens (in / cached / out) | 97,732 (96,204 / 82,538 / 1,528) | 112,286 (111,675 / 95,654 / 611) |
| cost | $0.0883 | $0.1806 |
| median wall time | 15.3s | 7.1s |
| passing runs per million tokens | 10.232 | 8.906 |
| median actions | 5.5 | 5 |
| explore / edit / verify / run | 55% / 18% / 27% / 0% | 60% / 20% / 20% / 0% |
| explored before first edit (median) | 3 | 3 |
| checked after its last edit | 2 of 2 | 2 of 2 |
| errors never come back to | 0 | 0 |
| median files / lines changed | 1 / 2 | 1 / 2 |
| runs that touched tests | 0 | 0 |
| said it was done, failed the check | 0 | 0 |

## Pairs (budget band ±10%)

| task | run | passed | tokens | matched | files both changed (identical) |
|---|---|---|---|---|---|
| bugfix-pricing | r1 | haiku ✓, sonnet ✓ | haiku 87,283, sonnet 111,993 | no (22%) | 1 (0) |
| bugfix-pricing | r2 | haiku ✓, sonnet ✓ | haiku 108,180, sonnet 112,579 | yes (4%) | 1 (1) |

Qualitative, not a benchmark: a few tasks and a few runs describe how these two agents worked on this work, with the intervals saying how little a pass rate on a small sample settles. Every number is read from the vendors' own streams and the harness's check.
