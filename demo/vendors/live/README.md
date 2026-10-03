# A recorded live duel

This directory holds a real run, not stand-in output. Claude Code 2.1.283
worked the demo bug (`../task.json`) twice on each of two models,
`claude-haiku-4-5-20251001` and `claude-sonnet-5`. `agentdiff duel` ran
them side by side, and each run was graded by `python3 -m unittest -q`.
All four runs passed.

| | haiku | sonnet |
|---|---|---|
| passed the check | 2 of 2 | 2 of 2 |
| cost (reported by the CLI) | $0.0883 | $0.1806 |
| median tokens per run | 97,732 | 112,286 |
| median wall time | 15.3 s | 7.1 s |

The two models wrote different, equivalent fixes (`diffs/`). Sonnet was
twice as fast; Haiku cost half as much and spent fewer tokens. Two runs
each is a description, not a ranking, and the report's intervals say so.

The Claude Code CLI authenticated through an endpoint its host configured
(`ANTHROPIC_BASE_URL`), not through a key in this repository. It ran with
the coding tools only and no MCP servers, and without the host session's
variables. The run before this one found that without that isolation the
agent under test inherited the host agent's session, and it was
discarded.

Before committing, every file here was scanned for secret-named
environment values and for account and session identifiers of the
environment it ran in; none were found.

What is here:

- `raw/`: every line the CLI printed, with its arrival time. This is the
  converter's fixture: `tests/test_vendors.py` re-converts these streams
  and checks that the traces match.
- `traces/`, `records/`, `diffs/`: what the harness wrote.
- `duel.json`, `DUEL.md`: the fair report.

To rebuild the report and the page without running anything:

```bash
agentdiff duel --from demo/vendors/live
```
