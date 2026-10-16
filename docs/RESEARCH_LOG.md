# Research log

What each research run read, what it took, and what it left and why.
Newest first. A later run never takes an idea an entry here rejected
unless something new changes the reason.

## 2026-10-16

**How it was read.** arXiv and the paper mirrors could not be reached
from the run's network. The papers below were read through search-engine
extracts of their abstracts and pages, not their full text. Numbers
quoted are theirs as the extracts gave them.

**Read:**
- *How Many Tasks Are Enough for Agent Benchmark Decisions? A Replay
  Analysis of Public LLM Agent Benchmarks* (W.-J. Huang, arXiv
  2607.12338). It replays completed task-level records of SWE-bench,
  AppWorld and tau-bench to ask when a partial run already supports the
  full run's pairwise decision: AppWorld at 15% of tasks, tau-bench at
  25%, SWE-bench Verified at 90%. A raw task fraction is not a decision
  rule. https://arxiv.org/abs/2607.12338
- *ParEvalLayer: When Partial LLM-Agent Evaluations Support a Decision*
  (arXiv 2608.02444). It reads paired task outcomes under a policy fixed
  in advance (improvement wanted, task order, decision-error target,
  allowed unresolved rate), and warns that a rule deciding only the easy
  pairs looks accurate while leaving many unresolved.
  https://arxiv.org/html/2608.02444
- *Self-Harness: Harnesses That Improve Themselves* (arXiv 2606.09498).
  A fixed model mines weaknesses from its own traces, proposes bounded
  edits, and accepts only edits that do not regress held-in or held-out
  splits: 40.5% → 61.9% on a 64-task Terminal-Bench 2.0 subset for one
  base model. https://arxiv.org/abs/2606.09498
- *Agentic Context Engineering* (ICLR 2026, arXiv 2510.04618), and the
  2026 follow-up on *scoped verification* of evolving contexts (arXiv
  2607.09175). Itemised delta edits, merged deterministically, instead
  of whole-prompt rewrites. https://arxiv.org/abs/2510.04618
- *Meta-Harness* (arXiv 2603.28052), *Agentic Harness Engineering*
  (arXiv 2604.25850) and *Evo²* (arXiv 2610.13025): harness search with
  an agentic proposer, a Pareto frontier of harnesses, and weight-harness
  co-evolution.
- Failure attribution: *Soap* (arXiv 2610.13105), *LongRCA Bench* (arXiv
  2608.15242), *Failure as a Process* (arXiv 2607.09510), *Conformal
  Agent Error Attribution* (arXiv 2605.06788).
- Cheaper evaluation: *EarlyEval* (arXiv 2609.02783) halts single runs
  on a learned prediction; *AV-AIVAT* (arXiv 2608.06362) stops paired
  comparisons with anytime-valid confidence sequences.

**Taken: stop a changed arm once the runs left cannot make it kept.**
The replay studies show partial runs often reach the full run's
decision, and estimate when, with an error target. AgentDiff's
keep-or-revert decision is a fixed rule over pass counts, so the same
question has an exact answer: bound each task's final count and ask the
rule. The verdict can't change, and the runs a lost cause would still
have made are skipped.
- Built: `selfevolve.could_keep`, `self_evolve(settle=True)`,
  `rounds=` on `vendor_arm`, and `--no-settle`.
- A test checks it against `decide()` on every completion of 400 random
  cases.
- Measured:
  - **Synthetic:** it skips 17–35% of a harmful change's runs, about 5%
    of a do-nothing change's, and 2–3% of a helpful one's. No verdict
    changed in 2,700 draws.
  - **Recorded:** 0 of the 8 real changes on disk would have stopped
    early. They were neutral or helpful.
- Cost: rounds wait for their slowest run, so a changed arm can take a
  little longer in wall-clock time.

**Not taken, and why:**
- **Statistical early stopping** (anytime-valid tests, EarlyEval's
  learned halting). It accepts an error rate, or lets a model's
  prediction decide when a run counts. Here no model may decide a
  number, and the exact rule above loses nothing.
- **Pareto frontier of harnesses** (Meta-Harness). It needs many
  candidate harnesses alive at once and many more real runs than a
  2-dollar run allows. A candidate for when the task sets are larger.
- **ACE-style itemised context edits.** The harness's instructions are
  already itemised and added one at a time, each kept only by a paired
  test. Nothing new to build.
- **Accept only on no regression across held-in and held-out splits**
  (Self-Harness). Already the rule in substance: "breaks no task" on the
  evolving tasks, and `--holdout` for unseen ones.
- **Spectral or conformal failure attribution** (Soap, conformal
  attribution). Both need step embeddings or a calibration set of
  labelled decisive steps that AgentDiff's traces do not carry.
  Revisit if a labelled set (LongRCA Bench) can be read here.
