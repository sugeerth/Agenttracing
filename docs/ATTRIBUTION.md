# Attribution and Credit Assignment for Agent Traces

*State of the art as of August 2026, read against what AgentDiff already ships.*

**Sourcing note.** The egress proxy in this environment blocks direct page fetches
(`arxiv.org`, `emergentmind.com`, `themoonlight.io` all returned `403 CONNECT tunnel failed`).
Every claim below is drawn from **search-result summaries and abstracts**, not from pages I
opened. Each URL is cited so it can be verified; treat quantitative figures as
abstract-level claims pending a read of the paper.

**What already exists** (do not re-recommend): `attribution.py` walks a heuristic
propagation chain from a root divergence using a 0.3 Jaccard input/output overlap;
`counterfactual.py` produces one splice estimate per failing pair; `issues.py` clusters
divergences into fingerprinted systematic issues; `similarity.py` gives four-facet
behavioral similarity; `uncertainty.py` fuses logprob telemetry into `flagged`/`silent`;
`statistics.py` provides Wilson intervals and a seeded bootstrap. The gap this document
addresses is **corpus-level attribution**: the engine currently attributes *within a pair*,
never *across a corpus*, and never to trajectory **attributes**.

---

## 1. Shapley and cooperative-game credit assignment

Shapley has arrived in the LLM-agent literature, but almost entirely on the *training* side.
Shapley-Coop redistributes payoffs among self-interested LLM agents so local incentives align
with the global goal ([arXiv 2506.07388](https://arxiv.org/pdf/2506.07388)); SHARP instantiates
a hierarchical credit functional with a Shapley approximation for multi-agent RL
([arXiv 2602.08335](https://arxiv.org/html/2602.08335v2)); "Who Gets the Reward & Who Gets the
Blame?" refines Shapley agent-level credit into per-message rewards that penalize redundancy and
sabotage ([arXiv 2511.10687](https://arxiv.org/html/2511.10687v3)). All three are *policy
optimization* papers. They compute Shapley by re-running or re-sampling the system.

The approximation family is well-characterized. TMC-Shapley (Ghorbani & Zou) samples random
permutations, accumulates marginal gains, and truncates once the marginal gain drops below a
tolerance ([pyDVL](https://pydvl.org/stable/value/shapley/),
[Data Shapley](https://www.emergentmind.com/papers/1904.02868)). KernelSHAP solves a weighted
least-squares problem over sampled coalitions and converges faster than naive Monte Carlo
([Interpretable ML Book, ch. 18](https://christophm.github.io/interpretable-ml-book/shap.html)).
Variance reduction is mature: permutation sampling, antithetic coupling (a permutation plus its
reverse), stratification by position, and control variates
([arXiv 2104.12199](https://arxiv.org/pdf/2104.12199),
[MDPI antithetic study](https://www.mdpi.com/2673-9909/3/4/49),
[VLDB survey](https://www.vldb.org/pvldb/vol18/p3077-xie.pdf),
[Shapley in ML survey](https://arxiv.org/pdf/2202.05594)).

**Determinism.** Sampling-based Shapley is *not* deterministic unless you fix the permutation
schedule. Two escapes preserve AgentDiff's guarantee: (a) **exact enumeration** — with n ≤ ~16
players, all 2^n coalitions are enumerable in stdlib and the answer is exact; (b) **Owen
values** over a fixed coalition structure, which restrict permutations to those respecting
groups, shrinking the player set dramatically
([BBVA](https://www.bbvaaifactory.com/shapley-and-owen-values-for-model-output-explainability-a-hands-on-case-study/)).
Both are reproducible without a seed.

**The real obstacle is not cost — it is `v(S)`.** Shapley needs a value for *every* coalition.
On a logged trace, `v(S)` for an arbitrary subset of steps does not exist; you either re-run the
agent (breaks the design principle) or you *define a surrogate* and be honest that Shapley then
attributes to your surrogate, not to the agent.

**What AgentDiff should do.** Do not do step-level Shapley over a single trace. Instead
compute **exact Shapley over divergence *regions*** (typically 2–8 per pair, so 2^n is trivial),
with the surrogate `v(S)` = the existing splice estimator from `counterfactual.py` applied to the
subset S of divergences adopted from the winner. This is a strict generalization of the current
single-splice counterfactual — it turns "what if B had made A's choice at the root" into "how
much of the outcome gap does each divergence deserve, accounting for interactions" — it is
exact, deterministic, stdlib-only, and it reuses machinery already written. Label the output
`splice-Shapley` in the schema so nobody reads it as a causal effect on the agent.

---

## 2. Counterfactual and causal attribution over trajectories

This is the hottest area and the one where AgentDiff's constraint bites hardest. Causal Agent
Replay models a run as a structural causal model, applies `do(·)` to a step, and **re-executes
the trajectory forward under the same stochastic policy**, measuring the shift in the outcome
distribution; it adds an intervention algebra, a "point-of-commitment" rule to resolve a
run-forward confound, and a budget-bounded Monte-Carlo Shapley estimator for interacting steps
([arXiv 2606.08275](https://arxiv.org/abs/2606.08275)). CausalFlow computes step-level Causal
Responsibility Scores, generates minimally edited repairs, and validates them by re-execution or
outcome prediction ([arXiv 2605.25338](https://arxiv.org/abs/2605.25338)). FAMAS — the first
spectrum-based failure attribution for multi-agent systems — estimates responsibility from
variation across **repeated executions** ([arXiv 2509.13782](https://arxiv.org/abs/2509.13782),
[FSE 2026](https://conf.researchr.org/details/fse-2026/fse-2026-research-papers/205/Spectrum-based-Failure-Attribution-for-Multi-Agent-Systems)).
The pattern is uniform: **every rigorous causal method in 2026 buys its rigor with re-execution.**

The baseline is bad enough to make this worth it. On Who&When, the best attribution method
reaches ~53.5% agent-level and ~14.2% step-level accuracy
([Who&When](https://ag2ai.github.io/Agents_Failure_Attribution/),
[benchmark follow-up](https://arxiv.org/html/2604.22708v1)); dynamic re-execution methods
reportedly gain ~10 points at step level precisely because they verify candidates. Purely
static/LLM-judge attribution is correlational and unreliable. Dependency-guided search (FALAT,
[arXiv 2606.00765](https://arxiv.org/pdf/2606.00765)) and temporal-semantic framing (StepFinder,
[arXiv 2606.03467](https://arxiv.org/html/2606.03467v1)) are the static alternatives.

The formal vocabulary worth borrowing is Halpern–Pearl. Degree of responsibility lies in [0,1] —
1 for a counterfactual cause, 0 for no causal influence — and degree of blame is expected
responsibility over an epistemic state
([JAIR](https://jair.org/index.php/jair/article/view/10386)); extensions cover decentralized
multi-agent settings ([arXiv 2204.00302](https://arxiv.org/pdf/2204.00302)) and human-AI
collaboration, which also warns that causality- and Shapley-based attribution
*disproportionately blames whoever contributed more*
([arXiv 2411.03275](https://arxiv.org/pdf/2411.03275)). Probabilities of necessity and
sufficiency (PN/PS/PNS) are only **bounded**, not point-identified, from observational data alone
([arXiv 2210.08874](https://arxiv.org/abs/2210.08874),
[closed-form bounds](https://arxiv.org/pdf/2505.15274)) — which is exactly AgentDiff's situation.

**What AgentDiff should do.** Adopt the *vocabulary* without the re-execution. With multi-run
traces (already collected by `runs`/`stability.py`), compute **PN and PS as bounded intervals**
over the corpus: PN ≈ among runs where attribute/divergence X occurred and the task failed, the
fraction of matched runs without X that succeeded; PS its mirror. Report them as intervals with
the identification assumption printed next to the number, never as points. Rename the existing
propagation chain's confidence field to make clear it is *dependency*, not *causation*.

---

## 3. RL credit assignment applied post-hoc

RUDDER redistributes a delayed episodic return to the state-action pairs that shifted the
expected return, via return decomposition and contribution analysis
([arXiv 1806.07857](https://arxiv.org/pdf/1806.07857),
[project page](https://ml-jku.github.io/rudder/)). Hindsight Credit Assignment learns a
hindsight probability of an action given current and future state, and COCOA extends it to
counterfactual contribution — "would the agent still have reached this reward had it acted
differently?" ([arXiv 2306.16803](https://arxiv.org/abs/2306.16803),
[CCA](https://arxiv.org/pdf/2011.09464)). The temporal credit assignment survey
([arXiv 2312.01072](https://arxiv.org/pdf/2312.01072)) and the 2026 LLM-specific survey
([arXiv 2604.09459](https://arxiv.org/pdf/2604.09459)) map the space.

**The honest read: none of this is stdlib-portable.** RUDDER's "no explicit reward model" claim
means no *hand-specified* reward model — it trains an LSTM return predictor, which is a learned
model, i.e. numpy/torch plus nondeterminism. HCA/COCOA learn a hindsight classifier. Applying
either offline also inherits off-policy evaluation's identification problem: when the logged
policy never took an action in a context, no estimator recovers the reward there — overlap
failure makes importance-weighted estimators unstable or undefined
([arXiv 2402.08201](https://arxiv.org/pdf/2402.08201),
[logging policy design](https://arxiv.org/html/2605.15108)). Agent logs additionally almost never
record action propensities, without which importance weighting is not even constructible.

**What AgentDiff should do.** Take **one idea** from this literature and drop the rest: RUDDER's
"key event = the point where expected return changes" reframed as a deterministic, corpus-level
statistic. For each step position (or step-type/tool bigram), compute the empirical success rate
of runs *conditional on having reached that state*, and report the largest drops as **credit
cliffs**. This is a difference of conditional means over logged runs — exact arithmetic, no model,
no seed. Explicitly *reject* RUDDER/HCA/COCOA proper as dependency-breaking.

---

## 4. Attribute-based attribution — the corpus-level answer

This is the strongest fit for AgentDiff and the least crowded.

Recent empirical work attributes outcomes to **trajectory attributes** rather than steps.
"Beyond Resolution Rates" analyzes 9,374 trajectories across 19 agents and 500 tasks and finds
(a) the LLM, not the framework, is the primary driver of both outcome and behavior, and
(b) **the widely reported correlation between trajectory length and failure reverses direction
once task difficulty is controlled — it is a confound**
([arXiv 2604.02547](https://arxiv.org/abs/2604.02547)). What survives control is *structure*:
agents that gather context before editing and invest in validation succeed more often. Related
work builds failure predictors from ~32 log-derived features — step count, tool-call counts, tool
entropy, tokens, wall time, repetition/loop signals, error rate — and shows lightweight logistic
regression predicts failure from features available after the *first* tool interaction
([arXiv 2511.00197](https://arxiv.org/pdf/2511.00197),
[TRACER](https://arxiv.org/pdf/2602.11409),
[What Resolve Rate Hides](https://arxiv.org/pdf/2607.06184)).

The statistical toolkit is entirely deterministic and stdlib-implementable: stratified
(difficulty-matched) contingency tables; a χ²/Fisher conditional-independence test per attribute;
one-way and two-way variance decomposition (η², ω²) attributing outcome variance to agent, task,
and attribute ([ANOVA assumptions](https://cran.r-project.org/web/packages/afex/vignettes/assumptions_of_ANOVAs.html));
logistic regression by IRLS in pure Python; permutation importance as a model-agnostic
alternative to SHAP ([Interpretable ML Book](https://christophm.github.io/interpretable-ml-book/shap.html)).
Uplift/CATE framing gives the right target quantity — the *effect* of an attribute, not its
correlation — but rests on the conditional independence (unconfoundedness) assumption
([scikit-uplift](https://www.uplift-modeling.com/en/latest/user_guide/introduction/cate.html)).
The failure mode is Simpson's paradox: a trend present in every stratum can reverse when strata
are pooled with unequal composition
([SEP](https://plato.stanford.edu/entries/paradox-simpson/)) — which is exactly the
trajectory-length reversal above.

**What AgentDiff should do.** Build an **attribute attribution table** as a first-class report
object. Derive attributes deterministically from the schema — step count, tool mix and tool
entropy, plan-before-act flag, plan depth, source-quality mix from `quality` annotations,
search/retrieve ratio, repetition/loop count, mean and minimum token probability from the
`model` block, divergence-issue fingerprints from `issues.py`. For each: raw success-rate delta,
**task-stratified** delta (stratify on `task.id`, which AgentDiff always has — this is a
Mantel–Haenszel-style pooled estimate and it is what kills the length confound), Wilson interval
via the existing `statistics.py`, a Fisher exact p-value, and a "reverses under stratification"
flag. Add a small deterministic logistic regression (fixed IRLS iterations, fixed tolerance)
over the same attributes to report which are jointly, rather than marginally, predictive. This
answers "which behavioral attributes predict failure across a corpus" exactly, offline,
reproducibly.

---

## 5. Attribute-based visual encoding

The canonical ranking is unchanged: Bertin's visual variables, ordered by Cleveland & McGill from
most to least accurate for quantities — position on a common scale, position on non-aligned
scales, length, direction, angle, slope, area, volume, shading, color saturation — with Munzner's
formalization splitting channel effectiveness by *magnitude* versus *identity* attributes
([graphical perception collation, arXiv 2109.01271](https://arxiv.org/pdf/2109.01271),
[course notes](https://homepage.divms.uiowa.edu/~luke/classes/STAT4580/percep.html)). The modern
qualification is that effectiveness is **not fixed** — task, data distribution, and context
mediate it; only position is consistently accurate.

Two 2026 results matter here. **Separability**: in a CHI 2026 study of bivariate symbol maps,
color × shape was the most separable channel pair and **size × orientation the least**, with
size × color and size × shape indistinguishable
([arXiv 2602.20022](https://arxiv.org/abs/2602.20022),
[CHI 2026](https://dl.acm.org/doi/10.1145/3772318.3790287)). **Machine cognition**: "Toward a
Machine Bertin" argues that VLMs now consume chart images in automated pipelines, process them
via patch tokenization rather than holistic perception, and fail on designs humans read easily —
so human-derived effectiveness rankings do not transfer
([arXiv 2602.01527](https://arxiv.org/abs/2602.01527)).

For many attributes at once, Borgo et al.'s glyph survey remains the reference — fourteen design
guidelines, and an explicit warning that small glyphs encode variables at low perceptual
precision, forcing hard channel-allocation trade-offs
([Borgo et al. STAR](https://www.cg.tuwien.ac.at/research/publications/2013/borgo-2013-gly/borgo-2013-gly-report.pdf)).
On chartjunk, the empirical picture is more nuanced than Tufte: Bateman et al. found embellished
charts no worse for accuracy and *significantly better* for long-term recall
([Bateman 2010](https://sites.stat.columbia.edu/gelman/communication/Bateman2010.pdf)), though
practitioners remain split ([arXiv 2009.02634](https://arxiv.org/pdf/2009.02634)). The
consensus that survives: decoration is not automatically harmful, but *encoding* channels should
still be spent on the highest-accuracy options available. Large value ranges (token counts,
cost) need explicit design attention, not a naive linear axis
([arXiv 2404.15150](https://arxiv.org/pdf/2404.15150)).

**What AgentDiff should do.** Render the §4 attribute table as a **Cleveland dot plot / forest
plot**: one row per attribute, point at the stratified success-rate delta, horizontal line for the
Wilson interval, vertical zero-reference line — position on a common scale for the quantity that
matters, which is the top-ranked channel, and it pairs naturally with uncertainty
([DescTools PlotDot](https://search.r-project.org/CRAN/refmans/DescTools/html/PlotDot.html)).
Sort by absolute effect. Use color hue *only* for the reverses-under-stratification flag
(identity channel, categorical) and never for magnitude. Explicitly avoid multi-attribute
glyphs on the step timeline: with 5+ attributes per step the precision is too low to be worth it,
and size × orientation — the tempting pairing for "cost × drift" — is the least separable pair
tested. Everything here is static SVG in the existing self-contained HTML; no library needed.

---

## Ranked implementation table

| # | Technique | Question answered | Cost | Deterministic | Stdlib-only | Effort |
|---|---|---|---|---|---|---|
| 1 | Task-stratified attribute attribution table (§4) | Which behavioral attributes predict failure across the corpus? | O(traces × attributes) | Yes | Yes | Medium |
| 2 | Attribute forest/dot plot (§5) | How large is each effect, and how sure are we? | Trivial | Yes | Yes | Small |
| 3 | Exact splice-Shapley over divergence regions (§1) | How is the outcome gap shared among a pair's divergences? | 2^n, n≤8 | Yes (exact) | Yes | Medium |
| 4 | Deterministic logistic regression over attributes (IRLS) (§4) | Which attributes matter *jointly*, not marginally? | O(iters × n × p) | Yes (fixed iters) | Yes | Medium |
| 5 | Credit cliffs — conditional success-rate drops by position/state (§3) | Where in a trajectory does expected success collapse? | O(steps) | Yes | Yes | Small |
| 6 | Bounded PN/PS over multi-run traces (§2) | Was this divergence necessary / sufficient for failure? | O(runs) | Yes | Yes | Medium |
| 7 | Spectrum-style suspiciousness (Ochiai/Tarantula over issue fingerprints × outcomes) (§2) | Which recurring issue co-occurs most with failure? | O(issues × runs) | Yes | Yes | Small |
| 8 | Permutation importance over attributes | Model-agnostic attribute ranking | O(perms × n) | Only with fixed schedule | Yes | Small |
| 9 | Sampling Shapley (TMC / KernelSHAP) over steps | Per-step credit at scale | High | **No** (seeded only) | KernelSHAP needs a lin-solver | Large |
| 10 | Causal Agent Replay / CausalFlow / FAMAS | True interventional step causality | Very high (LLM spend) | **No** | **No** — needs a runtime | Out of scope |
| 11 | RUDDER / HCA / COCOA | Learned return redistribution | Training run | **No** | **No** — needs a learned net | Out of scope |

**Rigorous on logs alone:** 1–8. **Secretly requires re-running the agent:** 10 (all three
systems re-execute; FAMAS needs *repeated* executions to build its spectrum). **Secretly requires
a learned reward/return model:** 11. Note that #3's rigor is conditional: it is exact *with
respect to the splice surrogate*, not with respect to the agent.

---

## Honest limits

Attribution over logged traces cannot establish **causation**, only dependency plus assumptions.
Four limits are structural, not fixable by better engineering:

1. **No overlap, no answer.** If a configuration never appears in the logs — an agent that never
   uses a tool, a source quality never seen on a task — nothing in the corpus identifies its
   effect. No estimator recovers information the logs do not contain
   ([arXiv 2402.08201](https://arxiv.org/pdf/2402.08201)).
2. **No propensities, no reweighting.** Agent traces do not record the probability with which the
   policy chose each action, so importance-weighted off-policy estimators cannot even be
   constructed ([logging policy design](https://arxiv.org/html/2605.15108)).
3. **Unmeasured confounding is unbounded.** Task difficulty already reverses the
   length↔failure relationship ([arXiv 2604.02547](https://arxiv.org/abs/2604.02547)); an
   unrecorded confounder can do the same to any attribute, and stratification only controls what
   is in the schema ([Simpson's paradox](https://plato.stanford.edu/entries/paradox-simpson/)).
4. **Counterfactuals are bounded, not identified.** PN/PS require experimental data for point
   identification; from observation alone only intervals are available
   ([arXiv 2210.08874](https://arxiv.org/abs/2210.08874)).

A fifth, softer limit: attribution methods based on actual causality and Shapley systematically
assign more blame to whoever contributed more, which is a fairness property, not a truth
property ([arXiv 2411.03275](https://arxiv.org/pdf/2411.03275)). AgentDiff should say
"associated with," "necessary-given-these-runs," or "shares X% of the modeled gap" — and reserve
"caused" for nothing it computes offline.
