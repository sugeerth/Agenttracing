# The diagnoser's benchmark — current scorecard and full history

**Scorecard as of 2026-09-03** (commit series ending 023e8dc). Every row is
a printed output of `agentdiff bench` or of `demo/external/whowhen.py`;
denominators are the number of scored pairs (kind) or the number of
pairs that have an agent step to name (step).

| corpus | condition | cause kind | decisive step exact | abstention | chain recall / precision | probe margin kind | probe margin step |
|---|---|---|---|---|---|---|---|
| handcrafted (20 pairs) | annotated | 20/20 | 16/16 | 4/4 | 0.928 / 0.931 | +0.40 | +0.19 |
| generated, 2,200 pairs, 18 families | annotated | 0.880 (1936/2200) | 1653/1832 (0.902) | 0.946 (348/368) | 0.874 / 0.963 | +0.32 | +0.07 |
| generated, 2,200 pairs, 18 families | stripped (no quality/note) | 0.840 (1847/2200) | 1361/1832 (0.743) | 0.946 (348/368) | 0.749 / 0.977 | +0.26 | +0.10 |
| generated, 300 pairs, 19 families (v41) | annotated | 0.887 (266/300) | 223/252 | — | — | +0.34 | +0.05 |
| generated, 300 pairs, 19 families (v41) | stripped | 0.853 (256/300) | 177/252 | — | — | +0.29 | +0.08 |

The 300-pair rows were measured after the v41 engine changes and again
with the previous engine on the *same* corpora: identical kind and step
outcomes, so the evidence-class tie-break and the generator registry
changed nothing that was already decided. The `overdetermined` family
(v41) is flagged 10/10 in both conditions and 0/290 elsewhere. Error
bars are clustered by cause family (×12.2 the naive bar at 2,200 pairs)
because scenarios in one family share a template.

## External validation: Who&When (2026-09-03)

The public Who&When failure-attribution dataset (Zhang et al., 2025;
184 annotated failing multi-agent logs — 126 algorithm-generated, 58
hand-crafted, each with a human-labelled `mistake_step` and
`mistake_agent`) was reachable read-only, converted with
`demo/external/whowhen.py` into SCHEMA traces (labels kept in a sidecar
the engine never sees), and scored.

**What could be scored.** The dataset ships failing logs only — no
passing twin — so the pairwise diagnoser, the decisive-step machinery
and the replay could not run. What was scored is the single-trace
*reading* (`agentdiff explain`): the step it points at first, against
the human label. Several pointer policies were tried; all are reported,
with the two floors any attributor has to clear.

| pointer policy (reading layer) | predicted | step exact | within ±1 |
|---|---|---|---|
| critical error (declared) | 42/184 | 2/184 (0.011) | 15/184 (0.082) |
| earliest observable finding | 161/184 | 12/184 (0.065) | 49/184 (0.266) |
| wrong-value origin (rests_on) | 3/184 | 0/184 | 0/184 |
| first unsupported answer atom | 22/184 | 5/184 (0.027) | 6/184 (0.033) |
| first / last error step | 70/184 | 2/184 / 0/184 | 37/184 (0.201) / 27/184 (0.147) |
| answer basis complete | 47/184 | 7/184 (0.038) | 8/184 (0.043) |
| shipped default (origin › critical › finding) | 161/184 | 8/184 (0.043) | 46/184 (0.250) |
| **floor: always the first step** | 184/184 | **20/184 (0.109)** | — |
| **floor: uniform guess, expected** | 184/184 | **17.5/184 (0.095)** | — |
| agent exact (shipped default) | 161/184 | 20/184 (0.109) | — |

**The result is negative and is reported as such.** No pointer policy of
the single-trace reading beats the always-first-step floor on exact
step localization (best 12/184 = 0.065 against a floor of 0.109);
agent identification (20/184) equals that floor. Within-±1 reaches
0.27. The hand-crafted subset is worse than the algorithm-generated one
(1/58 vs 7/126 exact for the shipped policy).

Why, honestly: these logs are prose between named agents with no tool
structure, no typed expected values the claim extractor recognises
(GAIA answers are names and counts), and labels that judge the
*content* of a reasoning step — exactly what a deterministic, lexical,
zero-dependency engine does not read. The engine was built for pairs
with recorded tool I/O; on that design it measures 0.88–0.90 above. The
number here says what it does *not* do, which is the point of running
it. It also matches the field's own finding that step-level attribution
on Who&When is hard (published accuracies peak at 14–30% with strong
LLM judges). Converting these logs into pairs — recording a passing run
of each task through the harness — is the next experiment, not a
change to this number.

## History: how the benchmark was built

The text below is the benchmark's history as it was written in the
README, iteration by iteration, kept verbatim.

### The diagnoser is itself benchmarked

Failure attributors that are never evaluated collapse quietly on hard
cases, so AgentDiff measures its own: `demo/diagnosis_bench/` ships 20
handcrafted trace pairs with one implanted known cause each (it began
as 12; the history below is how it grew) (grader mislabel,
harness kill, environment fault, wrong fact, blind write, pure
divergence) and `deepcompare/bench.py` scores whether the leading
hypothesis matches the implant — contested never counts as correct.
The benchmark's first run measured 10 of 12 and exposed a structural
blind spot (abandoned tool errors split their score across three
hypotheses); fixing that fusion rule brought the same untouched corpus
to 12 of 12, and the test suite holds the floor at 0.75 so a future
regression in diagnosis quality fails CI rather than shipping.

The corpus then grew to 18 scenarios along the axis the literature says
attributors collapse on: the **decisive error step** — Who&When's
counterfactual criterion, the earliest step whose correction flips the
outcome, where published step-level accuracy peaks at 14–30%. Six hard
scenarios reproduce the documented failure modes: a quiet early cause
whose loud downstream symptom is the trap, visible-but-non-causal
distractor pathologies, and Who&When-Pro-style faults injected after an
exactly-replayed successful prefix. The scorer now reports **kind
accuracy, step localization (exact and ±1), and abstention** — a grader
mislabel or harness kill has no agent step to correct, and naming one
there is a scored miss, not a near-hit. The hard corpus exposed two
more engine bugs before they were fixed (a shared fact both runs read
was being blamed as "the wrong fact"; a 70%-covered answer that flatly
contradicted the expected one still counted as a grader-suspect
"match"). Handcrafted corpus: kind 20/20, step 16/16 exact, abstention
4/4, chain recovery 0.93 recall / 0.93 precision (the bench prints
0.9281 / 0.9313).

The benchmark then went procedural: `demo/diagnosis_bench/
generate_scale.py --pairs N` composes eighteen cause families across
domains, trace lengths and distractors with mechanically derived truth
(seeded, byte-identical per N; `agentdiff bench <dir> --strict` gates on
the shared floors). Four families — `negation_answer`, `wrong_entity`,
`causal_duplicate`, `garbage_args` — come from an independent
adversarial evaluation that built trace pairs to make the engine tell
confident wrong stories; the fixes it forced are exclusivity rules, not
scenario patches, and the attack pairs are pinned as regression
fixtures. A sixteenth family is the Agentic Benchmark Checklist's
control — `null_agent`, a do-nothing run that restates the prompt,
which a diagnoser must never abstain on or blame the grader for. Two
more are *decoys* built against the benchmark's own leak (below):
`late_decision`, where the run diverges harmlessly two value-free
reads before the real cause enters, and `misread_reason`, where the
true value is observed and then misread one benign remark later — in
both, the first step without a twin is the wrong answer. At 2,200
generated pairs the measured scorecard is **cause kind 0.880
(1936/2200), decisive step 1653/1832 exact, abstention 0.946, chain
recovery 0.874 recall / 0.963 precision**: the ten original families,
the two corrected adversarial ones, the control and both decoys sit at
1.0, and every miss concentrates in the three named open challenges —
`paraphrase_grader` 0.84 (reworded-but-correct answers),
`negation_answer` 0.55 (the failing answer negates the expected one
while reusing its tokens) and `garbage_args` 0.0 (a tool correctly
rejecting an agent-invented argument looks environmental; the engine
honestly contests rather than confirming either story). They stay in
the measured number because a benchmark containing only what the
diagnoser already gets right measures nothing.

The benchmark audits itself before it scores. Every pair passes an
**injection contract**: the clean twin must pass its own grader (else
the pair is excluded and counted — it would measure the grader, not
the diagnoser), and the implanted artifact must be textually reachable
between the decisive step and the answer (1,788 artifacts checked at
2,200 pairs). And a **leakage probe** — a deliberately dumb detector
using surface cues only, no engine — is scored on every corpus, because
an implanted benchmark leaks fingerprints a detector can exploit
without doing causal work (Leaky Model Organisms, 2026). The headline
is the engine's margin over the probe, not its score: on cause
identification the margin is solid (**+0.32 annotated, +0.26
stripped**; the probe reaches 0.56). On decisive-step localization the
first measurement was the useful one: **+0.09 annotated** and, on the
stripped corpus, the probe *matched* the engine (0.75 vs 0.73, margin
−0.02) — for the original families the decisive step was simply the
first step without a twin in the passing run, so the step benchmark
leaked. The corpus was fixed, not eased: the two decoy families put
the decisive step *after* the first novel step, and the engine gained
one principled rule to meet them — a divergence whose intermediate
steps carried nothing forward (no write, no value, no number, no word
overlap with anything downstream) re-anchors to the step where the
wrong fact entered, by the same counterfactual criterion that defines
the decisive step. Re-measured at 2,200: step margin **+0.07
annotated, +0.10 stripped** (probe 0.64 vs engine 0.74). The rule's
first cut moved a real demo pair's anchor off a wrong calculation
because the value travelled under another surface form; the guard
that fixed it (any emitted value is consequential) is pinned as a
test. The valueless ticket domain, where no typed wrong fact exists to
re-anchor on, is the measured remainder: adjacent by one step.

Every scorecard accuracy now carries two error bars — naive, and
clustered by cause family, because scenarios in one family share a
template and are not independent draws; the clustered one is the honest
one, and the ratio between them is printed — at 2,200 pairs it is
**×12.2** (naive ±0.007, clustered ±0.085), because accuracy is bimodal
by family and the naive bar pretends the families are independent. The same discipline runs
through `runs` and `fleet`: two agents on the same tasks is a *paired*
design, so they report a paired difference with its standard error and
an exact sign test on the discordant tasks, and refuse to call a winner
below ten paired tasks.

The same corpus regenerated with `--strip-annotations` — every step's
`error`/`quality`/`note` nulled, so the engine must infer everything
from observation text — is the de-circularized condition the
adversarial evaluation asked for (the generator writes the very flags
the engine reads): **cause kind 0.840, decisive step 0.743 exact,
chain recovery 0.749 recall**, published beside the annotated numbers. The gap between the two scorecards
is the measured value of structured step metadata, not noise. The
scaled sweep is the loop's teacher: it caught an incoherent implant, a
coverage rule that credited wrong-valued answers, an alignment artefact
fixed by the twin rule (a step the other run also took verbatim cannot
be the anchor, EXCEPT a write the failing run performed more times),
shared-flag inversions (a pathology both runs exhibit cannot explain a
one-sided failure), and the paraphrase blind spot that became the
typed-value grader rule. All corpora are synthetic: they prove the
machinery against known ground truth, they do not claim field accuracy.

