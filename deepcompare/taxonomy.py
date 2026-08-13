"""AgentDiff's bespoke labels, mapped onto the two published failure taxonomies.

AgentDiff names failures in a vocabulary it invented for itself: divergence
``kind`` (``retrieval``, ``tool_selection``, ``tool_execution``, ``planning``,
``reasoning``, ``stopping``) and the process pathology flags in
:mod:`deepcompare.process`.  Those names are useful inside this tool and
useless outside it.  A finding phrased as "3 tool_execution divergences" cannot
be compared against anyone else's numbers, cannot be cited, and cannot be
aggregated with the field's own measurements of how agents fail.  This module
translates, and — more importantly — reports honestly where the translation
does not exist.

The two targets:

**MAST**, the Multi-Agent System Failure Taxonomy (arXiv 2503.13657).  Built by
grounded-theory coding of 150 traces across 7 agentic frameworks by 6 expert
annotators, reaching inter-annotator agreement of **Cohen's κ = 0.88**; an LLM
judge validated at **94% accuracy / κ = 0.77** against those humans was then
used to scale the labelling to 1,600+ traces.  Its 14 modes sit in three
categories whose measured mass is roughly **41.8% Specification & System
Design / 36.9% Inter-Agent Misalignment / 21.3% Task Verification &
Termination**.

**TRAIL** (arXiv 2505.08638), a three-level tree of 20 leaves under three
branches — Reasoning Errors, System Execution Errors, Planning and
Coordination Errors — annotated on long agentic traces.

They were published roughly two months apart and they genuinely disagree.  The
disagreements are encoded here rather than smoothed away, because smoothing
them is the failure mode of every "unified taxonomy" table:

1. **MAST has no bucket for system-execution failure at all.**  Rate limits,
   5xx responses, auth failures, timeouts — TRAIL spends 8 of its 20 leaves on
   them; MAST treats infrastructure as out of scope for *why agent systems
   fail*.  An unrecovered 503 is unlabelled in MAST.
2. **TRAIL has no verification category.**  MAST attributes **21.3%** of all
   observed failures to weak verification (3.2) or no/incorrect verification
   (3.3).  TRAIL has no leaf for "the agent never checked".
3. **Neither models fault attribution.**  Only tau-bench / tau2-bench treat
   *who is at fault* (user simulator vs. agent vs. environment) as an
   orthogonal dimension.  MAST and TRAIL fold it into the category, so neither
   can express "the agent was right and the user simulator was broken".

So each taxonomy is blind to about a fifth of the failure mass the other
captures.  **Neither is a superset of the other**, and a mapping that silently
picks one to report is wrong.  Everything here therefore emits both, side by
side, with the blind spots named.

Two further limits are structural to *this tool*, not to the taxonomies:

* AgentDiff compares **single-agent** trajectories.  MAST's entire category 2
  (Inter-Agent Misalignment, ~36.9% of its mass) needs at least two agents
  exchanging messages, and is therefore unreachable from an AgentDiff trace —
  reported as unreachable, never force-fitted onto a single-agent signal.
* Several modes in both taxonomies are **judge calls**: goal drift, weak
  verification, action-reasoning mismatch, instruction non-compliance.  They
  require reading intent against behaviour.  AgentDiff's signals are counts,
  containment tests and sequence comparisons; they cannot reach those modes,
  and say so with a reason rather than reaching anyway.

Finally, the standing caveat that every consumer of this module must carry:
**these are rule-based mappings of deterministic signals, not taxonomy
labels.**  MAST's numbers come from human annotators and a validated judge;
TRAIL's come from expert annotation.  Nothing here reads a trace for meaning.
The output is comparable to those papers *in vocabulary*, not in method, and
must never be presented as a MAST or TRAIL measurement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from .trace import Trajectory

# --------------------------------------------------------------------------
# Provenance — carried in data so it travels with any rendered output.
# --------------------------------------------------------------------------

#: Where each taxonomy comes from and how it was validated.  Kept beside the
#: mapping so a table rendered from this module cannot lose its citation.
PROVENANCE: dict = {
    "MAST": {
        "arxiv": "2503.13657",
        "name": "Multi-Agent System Failure Taxonomy",
        "method": (
            "grounded-theory coding of 150 traces across 7 frameworks by 6 expert "
            "annotators; inter-annotator agreement Cohen's kappa = 0.88; scaled to "
            "1,600+ traces with an LLM judge validated at 94% accuracy, kappa = 0.77"
        ),
        "modes": 14,
        "category_mass": {
            "Specification & System Design": 0.418,
            "Inter-Agent Misalignment": 0.369,
            "Task Verification & Termination": 0.213,
        },
    },
    "TRAIL": {
        "arxiv": "2505.08638",
        "name": "TRAIL: Trace Reasoning and Agentic Issue Localization",
        "method": "expert annotation of long agentic traces against a 3-level error tree",
        "leaves": 20,
        "branch_leaves": {
            "Reasoning Errors": 8,
            "System Execution Errors": 8,
            "Planning and Coordination Errors": 4,
        },
    },
}

#: Confidence levels a mapping entry may carry.  ``none`` is a first-class
#: result: it names the nearest code in a taxonomy *and refuses it*, with the
#: reason.  An empty cell is information; an invented mapping is not.
CONFIDENCES = ("direct", "partial", "none")

#: Why a mode cannot be reached from an AgentDiff trace.  Four different
#: kinds of impossibility, which readers need to tell apart: two are fixable
#: by richer traces, two are not fixable without a judge or a redesign.
BLOCKERS = {
    "multi_agent": "needs two or more agents exchanging messages; AgentDiff traces one agent",
    "judge": "needs a model or human to read intent against behaviour; AgentDiff only counts",
    "outside_trace": "the evidence lives outside the step log AgentDiff is given",
    "label_vocabulary": "the signal exists in the trace but AgentDiff's label discards the distinction",
}


# --------------------------------------------------------------------------
# The taxonomies, as data.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MastMode:
    """One MAST failure mode: its paper code, name, and parent category."""

    code: str
    name: str
    category: str

    def to_dict(self) -> dict:
        return {"code": self.code, "name": self.name, "category": self.category}


@dataclass(frozen=True)
class TrailLeaf:
    """One TRAIL leaf, with the branch and group it hangs under.

    ``code`` is a stable dotted slug assigned *by this module* for
    referencing; TRAIL names its categories rather than numbering them, and
    inventing paper-looking numbers would be a citation that does not exist.
    """

    code: str
    name: str
    branch: str
    group: str

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "branch": self.branch,
            "group": self.group,
        }


_SPEC = "Specification & System Design"
_INTER = "Inter-Agent Misalignment"
_VERIF = "Task Verification & Termination"

#: MAST's 14 modes, in paper order.
MAST_MODES: tuple[MastMode, ...] = (
    MastMode("1.1", "Disobey Task Specification", _SPEC),
    MastMode("1.2", "Disobey Role Specification", _SPEC),
    MastMode("1.3", "Step Repetition", _SPEC),
    MastMode("1.4", "Loss of Conversation History", _SPEC),
    MastMode("1.5", "Unaware of Termination Conditions", _SPEC),
    MastMode("2.1", "Conversation Reset", _INTER),
    MastMode("2.2", "Fail to Ask for Clarification", _INTER),
    MastMode("2.3", "Task Derailment", _INTER),
    MastMode("2.4", "Information Withholding", _INTER),
    MastMode("2.5", "Ignored Other Agent's Input", _INTER),
    MastMode("2.6", "Action-Reasoning Mismatch", _INTER),
    MastMode("3.1", "Premature Termination", _VERIF),
    MastMode("3.2", "Weak Verification", _VERIF),
    MastMode("3.3", "No or Incorrect Verification", _VERIF),
)

_REASONING = "Reasoning Errors"
_SYSTEM = "System Execution Errors"
_PLANNING = "Planning and Coordination Errors"

#: TRAIL's 20 leaves, grouped by branch and mid-level group.
TRAIL_LEAVES: tuple[TrailLeaf, ...] = (
    TrailLeaf("reasoning.hallucination.language_only",
              "Language-only Hallucination", _REASONING, "Hallucinations"),
    TrailLeaf("reasoning.hallucination.tool_related",
              "Tool-related Hallucination", _REASONING, "Hallucinations"),
    TrailLeaf("reasoning.information_processing.poor_information_retrieval",
              "Poor Information Retrieval", _REASONING, "Information Processing"),
    TrailLeaf("reasoning.information_processing.tool_output_misinterpretation",
              "Tool Output Misinterpretation", _REASONING, "Information Processing"),
    TrailLeaf("reasoning.decision_making.incorrect_problem_identification",
              "Incorrect Problem Identification", _REASONING, "Decision Making"),
    TrailLeaf("reasoning.decision_making.tool_selection_errors",
              "Tool Selection Errors", _REASONING, "Decision Making"),
    TrailLeaf("reasoning.output_generation.formatting_errors",
              "Formatting Errors", _REASONING, "Output Generation"),
    TrailLeaf("reasoning.output_generation.instruction_non_compliance",
              "Instruction Non-compliance", _REASONING, "Output Generation"),
    TrailLeaf("system_execution.configuration.tool_definition_issues",
              "Tool Definition Issues", _SYSTEM, "Configuration"),
    TrailLeaf("system_execution.configuration.environment_setup_errors",
              "Environment Setup Errors", _SYSTEM, "Configuration"),
    TrailLeaf("system_execution.api_issues.rate_limiting",
              "Rate Limiting", _SYSTEM, "API Issues"),
    TrailLeaf("system_execution.api_issues.authentication_errors",
              "Authentication Errors", _SYSTEM, "API Issues"),
    TrailLeaf("system_execution.api_issues.service_errors",
              "Service Errors", _SYSTEM, "API Issues"),
    TrailLeaf("system_execution.api_issues.resource_not_found",
              "Resource Not Found", _SYSTEM, "API Issues"),
    TrailLeaf("system_execution.resource_management.resource_exhaustion",
              "Resource Exhaustion", _SYSTEM, "Resource Management"),
    TrailLeaf("system_execution.resource_management.timeout_issues",
              "Timeout Issues", _SYSTEM, "Resource Management"),
    TrailLeaf("planning.context_management.context_handling_failures",
              "Context Handling Failures", _PLANNING, "Context Management"),
    TrailLeaf("planning.context_management.resource_abuse",
              "Resource Abuse", _PLANNING, "Context Management"),
    TrailLeaf("planning.task_management.goal_deviation",
              "Goal Deviation", _PLANNING, "Task Management"),
    TrailLeaf("planning.task_management.task_orchestration",
              "Task Orchestration", _PLANNING, "Task Management"),
)

MAST_BY_CODE: dict[str, MastMode] = {mode.code: mode for mode in MAST_MODES}
TRAIL_BY_CODE: dict[str, TrailLeaf] = {leaf.code: leaf for leaf in TRAIL_LEAVES}

#: AgentDiff's own vocabularies, restated here so a drift between this module
#: and its sources is a test failure rather than a silent hole in the table.
DIVERGENCE_KINDS = (
    "retrieval", "tool_selection", "tool_execution",
    "planning", "reasoning", "stopping",
)
PROCESS_FLAGS = (
    "false_success", "looped", "loop_block", "repeated_calls",
    "no_information_steps", "swallowed_error", "blind_write",
    "budget_pressure", "undeclared_tools", "invented_arguments",
    "schema_violation",
)


# --------------------------------------------------------------------------
# The mapping.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Mapping:
    """One AgentDiff signal aimed at one taxonomy code.

    ``confidence`` is the whole point of the row.  ``direct`` means the
    taxonomy's own definition describes what AgentDiff detected.  ``partial``
    means the code is the closest available and the fit is imperfect in a way
    ``why`` states.  ``none`` names the nearest code and *declines* it — the
    signal does not license that label, and pretending otherwise would put
    fabricated mass into someone's citation.
    """

    signal: str
    source: str  # "divergence" | "process_flag"
    taxonomy: str  # "MAST" | "TRAIL"
    code: str
    confidence: str
    why: str

    def to_dict(self) -> dict:
        entry = MAST_BY_CODE[self.code] if self.taxonomy == "MAST" else TRAIL_BY_CODE[self.code]
        return {
            "signal": self.signal,
            "source": self.source,
            "taxonomy": self.taxonomy,
            "code": self.code,
            "name": entry.name,
            "confidence": self.confidence,
            "why": self.why,
        }


def _m(signal, source, taxonomy, code, confidence, why) -> Mapping:
    return Mapping(signal, source, taxonomy, code, confidence, why)


#: Divergence kind -> taxonomy codes.  A divergence is a *comparative* signal:
#: it says two runs parted company here, not that either one was wrong.  That
#: is why so few of these are ``direct`` — the taxonomies label a single run's
#: error, and a difference is weaker evidence than an error.
_DIVERGENCE_MAP: tuple[Mapping, ...] = (
    # retrieval -----------------------------------------------------------
    _m("retrieval", "divergence", "TRAIL",
       "reasoning.information_processing.poor_information_retrieval", "direct",
       "A retrieval divergence is exactly a difference in which source was searched, "
       "selected or read; TRAIL names that leaf and AgentDiff's quality annotation "
       "says which side took the weaker one."),
    _m("retrieval", "divergence", "MAST", "1.1", "partial",
       "MAST has no retrieval-quality mode at all. A weaker source becomes a MAST "
       "failure only once it makes the output violate the task specification, which "
       "the divergence alone does not establish."),
    # tool_selection ------------------------------------------------------
    _m("tool_selection", "divergence", "TRAIL",
       "reasoning.decision_making.tool_selection_errors", "direct",
       "Two runs reaching for different tools at the same point is the definition of "
       "the leaf; the failing side's choice is the selection error."),
    _m("tool_selection", "divergence", "MAST", "1.1", "partial",
       "Picking the wrong tool is a specification violation in MAST only when the "
       "specification named the tool. AgentDiff does not see the specification, so "
       "the fit is by analogy."),
    # tool_execution ------------------------------------------------------
    _m("tool_execution", "divergence", "TRAIL",
       "reasoning.hallucination.tool_related", "partial",
       "Same tool, different arguments: often the arguments were invented, which is "
       "the leaf — but a merely wrong argument that came from a real observation is "
       "not a hallucination, and the divergence cannot tell the two apart."),
    _m("tool_execution", "divergence", "TRAIL",
       "reasoning.information_processing.tool_output_misinterpretation", "partial",
       "The other reading of the same evidence: both runs called the tool, one used "
       "its result wrongly. Which reading is right needs the observation's meaning, "
       "which is a judge call."),
    _m("tool_execution", "divergence", "MAST", "1.1", "partial",
       "MAST folds bad tool use into 1.1 when it breaks a stated constraint. Nothing "
       "closer exists, and MAST offers no execution-level mode."),
    # planning ------------------------------------------------------------
    _m("planning", "divergence", "TRAIL",
       "planning.task_management.task_orchestration", "partial",
       "Two runs sequencing the work differently is an orchestration difference; "
       "whether the divergent plan is the *wrong* plan is not decidable from the "
       "step types."),
    _m("planning", "divergence", "TRAIL",
       "reasoning.decision_making.incorrect_problem_identification", "partial",
       "A plan divergence sometimes traces back to framing the task differently, "
       "but a different plan for the same framing looks identical here."),
    _m("planning", "divergence", "MAST", "2.3", "none",
       "MAST's planning-adjacent mode is Task Derailment, which sits in the "
       "inter-agent category and needs a goal the run can be judged to have drifted "
       "from. A single-agent plan-step difference is not evidence of derailment, and "
       "labelling it so would inflate MAST's category 2 from a signal that cannot "
       "reach it."),
    # reasoning -----------------------------------------------------------
    _m("reasoning", "divergence", "TRAIL",
       "reasoning.decision_making.incorrect_problem_identification", "partial",
       "Divergent reasoning steps are the nearest observable to a different problem "
       "framing; AgentDiff compares the text, not the inference."),
    _m("reasoning", "divergence", "MAST", "2.6", "none",
       "The nearest MAST mode is Action-Reasoning Mismatch, which requires reading a "
       "run's stated reasoning against the action it then took. Two agents reasoning "
       "differently is not a mismatch within either of them. Unmapped by design."),
    # stopping ------------------------------------------------------------
    _m("stopping", "divergence", "MAST", "1.5", "partial",
       "One side working on after the other had finished the same work is the "
       "deterministic shadow of not recognising a termination condition — though "
       "MAST's 1.5 presumes a condition was stated, and AgentDiff infers it from the "
       "other agent's behaviour instead."),
    _m("stopping", "divergence", "MAST", "3.1", "partial",
       "Read from the other side, the shorter run stopped first; when that shorter "
       "run is the one that failed, this is Premature Termination. The divergence "
       "kind alone does not say which side was right, so the outcome has to be "
       "consulted and the label stays partial."),
    _m("stopping", "divergence", "TRAIL",
       "planning.context_management.resource_abuse", "partial",
       "Extra steps past sufficiency spend budget for nothing, which is the closest "
       "TRAIL leaf. TRAIL has no premature-termination leaf at all, so the "
       "stop-too-early half of this signal is untranslatable into TRAIL."),
)

#: Process flag -> taxonomy codes.  Process flags are single-run signals and
#: map more cleanly than divergences: they assert something about one
#: trajectory rather than about a difference between two.
_PROCESS_MAP: tuple[Mapping, ...] = (
    # false_success -------------------------------------------------------
    _m("false_success", "process_flag", "TRAIL",
       "reasoning.hallucination.language_only", "direct",
       "Asserting an action that the write ledger shows never happened is a "
       "language-only hallucination in the plainest sense."),
    _m("false_success", "process_flag", "MAST", "3.3", "partial",
       "An unverified completion claim is what 'No or Incorrect Verification' "
       "describes; MAST reaches that label with a judge reading the trace, this "
       "reaches it from a contradiction between the answer text and the writes."),
    _m("false_success", "process_flag", "MAST", "1.1", "partial",
       "The task asked for an action that did not occur, which is also a task "
       "specification violation. MAST's modes are not mutually exclusive here."),
    # looped --------------------------------------------------------------
    _m("looped", "process_flag", "MAST", "1.3", "direct",
       "The same call returning the same result again is Step Repetition as MAST "
       "defines it — the one mode AgentDiff can count exactly."),
    _m("looped", "process_flag", "TRAIL",
       "planning.context_management.resource_abuse", "partial",
       "TRAIL has no repetition leaf; a cycle registers there only as budget spent "
       "for no progress."),
    # loop_block ----------------------------------------------------------
    _m("loop_block", "process_flag", "MAST", "1.3", "direct",
       "A repeated k-gram of calls is the sequence form of Step Repetition, which is "
       "what MAST 1.3 is defined on."),
    _m("loop_block", "process_flag", "TRAIL",
       "planning.context_management.resource_abuse", "partial",
       "Same limitation as a single cycle: TRAIL sees the cost, not the repetition."),
    # repeated_calls ------------------------------------------------------
    _m("repeated_calls", "process_flag", "MAST", "1.3", "direct",
       "The same call made twice with no error in between is Step Repetition. "
       "Retries after an error are deliberately excluded upstream, so this flag does "
       "not conflate recovery with looping."),
    _m("repeated_calls", "process_flag", "TRAIL",
       "planning.context_management.resource_abuse", "partial",
       "Registers in TRAIL only as wasted budget."),
    # no_information_steps ------------------------------------------------
    _m("no_information_steps", "process_flag", "TRAIL",
       "planning.context_management.context_handling_failures", "partial",
       "Re-deriving an observation already in context is a context-handling failure, "
       "but so is deliberate corroboration; the flag cannot separate them."),
    _m("no_information_steps", "process_flag", "MAST", "1.3", "partial",
       "Not literal step repetition — the call was new, only its result was old — so "
       "MAST's 1.3 fits the effect and not the definition."),
    # swallowed_error -----------------------------------------------------
    _m("swallowed_error", "process_flag", "TRAIL",
       "system_execution.api_issues.service_errors", "partial",
       "An error observation that was never recovered from. TRAIL would want to know "
       "whether it was a 5xx, a rate limit, an auth failure or a missing resource; "
       "AgentDiff's flag is one boolean and discards the distinction."),
    _m("swallowed_error", "process_flag", "TRAIL",
       "reasoning.information_processing.tool_output_misinterpretation", "partial",
       "Continuing past an error as though it succeeded is also a misreading of the "
       "tool's output; which leaf applies depends on whose fault the error was, and "
       "no taxonomy here has a fault axis."),
    _m("swallowed_error", "process_flag", "MAST", "3.3", "none",
       "MAST has no system-execution category whatsoever — an unrecovered "
       "infrastructure error is outside its scope by construction, not by oversight. "
       "3.3 would apply only if the agent had verified and got it wrong, which this "
       "flag does not establish. This is disagreement #1 between the taxonomies, and "
       "it is left as an empty cell on purpose."),
    # blind_write ---------------------------------------------------------
    _m("blind_write", "process_flag", "MAST", "3.3", "partial",
       "Writing before reading anything is acting without checking state, which is "
       "the absent-verification mode — though MAST usually means verification of the "
       "output, and this is verification of the precondition."),
    _m("blind_write", "process_flag", "TRAIL",
       "reasoning.decision_making.incorrect_problem_identification", "none",
       "TRAIL has no verification branch at all. This flag is squarely inside the "
       "21.3% of failure mass MAST attributes to weak or absent verification, and "
       "TRAIL cannot express it. Declining the nearest decision-making leaf rather "
       "than borrowing it is the honest result — this is disagreement #2."),
    # budget_pressure -----------------------------------------------------
    _m("budget_pressure", "process_flag", "TRAIL",
       "system_execution.resource_management.resource_exhaustion", "direct",
       "A run that spent 80% or more of its declared step budget is resource "
       "exhaustion measured against the harness's own limit."),
    _m("budget_pressure", "process_flag", "MAST", "1.5", "partial",
       "Finishing at the ceiling suggests the run did not know when to stop, but "
       "MAST 1.5 is about missing a stated termination condition and a step ceiling "
       "is the harness's condition, not the task's."),
    # undeclared_tools ----------------------------------------------------
    _m("undeclared_tools", "process_flag", "TRAIL",
       "system_execution.configuration.tool_definition_issues", "direct",
       "A call to a tool that was never offered is a tool-definition mismatch "
       "between what the agent believed it had and what the harness declared."),
    _m("undeclared_tools", "process_flag", "TRAIL",
       "reasoning.hallucination.tool_related", "partial",
       "The same event read as the agent's fault instead of the configuration's: "
       "inventing a tool is the canonical tool hallucination. AgentDiff cannot "
       "attribute between the two readings."),
    _m("undeclared_tools", "process_flag", "MAST", "1.1", "partial",
       "MAST absorbs this into disobeying the task specification; it has no "
       "configuration mode to put it in."),
    # invented_arguments --------------------------------------------------
    _m("invented_arguments", "process_flag", "TRAIL",
       "reasoning.hallucination.tool_related", "direct",
       "An argument value that appears in no prior observation and not in the prompt "
       "was produced by the model, not read from the world. That is the leaf, and "
       "the check is a containment test rather than an opinion."),
    _m("invented_arguments", "process_flag", "MAST", "1.1", "partial",
       "MAST would call a fabricated argument a specification violation; it has no "
       "hallucination mode of its own."),
    # schema_violation ----------------------------------------------------
    _m("schema_violation", "process_flag", "TRAIL",
       "reasoning.output_generation.formatting_errors", "partial",
       "Arguments that do not typecheck against the declared schema are malformed "
       "output; TRAIL's leaf is written about final output format rather than about "
       "call arguments."),
    _m("schema_violation", "process_flag", "TRAIL",
       "system_execution.configuration.tool_definition_issues", "partial",
       "The nearest configuration leaf, but pointed the wrong way: TRAIL's leaf "
       "blames the tool definition, and here the definition is fine and the call is "
       "wrong."),
    _m("schema_violation", "process_flag", "MAST", "1.1", "partial",
       "Violating a declared parameter contract is a specification violation, "
       "reached here by typechecking rather than by judgement."),
)

#: The whole table, divergences first then process flags, in signal order.
MAPPINGS: tuple[Mapping, ...] = _DIVERGENCE_MAP + _PROCESS_MAP


def mapping_table(source: Optional[str] = None) -> list[dict]:
    """The mapping as a flat, sorted list of dicts, for rendering or export.

    Sorted by (source, signal position, taxonomy, code) so any table built
    from it is byte-stable across runs.
    """
    order = {name: i for i, name in enumerate(DIVERGENCE_KINDS + PROCESS_FLAGS)}
    rows = [m for m in MAPPINGS if source is None or m.source == source]
    rows.sort(key=lambda m: (m.source, order.get(m.signal, 999), m.taxonomy, m.code))
    return [m.to_dict() for m in rows]


def mappings_for(signal: str) -> list[Mapping]:
    """Every mapping entry for one AgentDiff signal, including the refusals."""
    return [m for m in MAPPINGS if m.signal == signal]


# --------------------------------------------------------------------------
# Coverage — what fraction of each taxonomy this tool can reach at all.
# --------------------------------------------------------------------------

#: Modes no AgentDiff signal reaches, each with the reason and the blocker
#: class.  Written out rather than derived so that the *reason* survives: a
#: mode absent from the mapping table is indistinguishable from a mode nobody
#: got round to mapping, and those are very different facts.
_MAST_UNREACHABLE: dict[str, tuple[str, str]] = {
    "1.2": ("multi_agent",
            "Role specifications exist only when several agents are given distinct "
            "roles; an AgentDiff trace has one agent and no role field to disobey."),
    "1.4": ("outside_trace",
            "The trace records what the agent did, not what remained in its context "
            "window. A step that re-fetches known information is equally consistent "
            "with lost history and with deliberate corroboration."),
    "2.1": ("multi_agent",
            "A conversation reset is an event between agents or turns that the "
            "single-run step log does not contain."),
    "2.2": ("multi_agent",
            "Needs a counterparty who could have been asked. AgentDiff traces carry "
            "a task prompt, not a user-simulator channel, so 'should have asked' is "
            "not observable."),
    "2.3": ("judge",
            "Task derailment is drift away from the stated goal, which requires "
            "judging the goal against the trajectory. Divergence between two agents "
            "says they differ, not that either drifted."),
    "2.4": ("multi_agent",
            "Withholding requires a recipient who was owed the information."),
    "2.5": ("multi_agent",
            "Requires another agent's input to have been ignored."),
    "2.6": ("judge",
            "Action-reasoning mismatch means the stated reasoning and the action "
            "taken disagree. Detecting that is reading comprehension over the "
            "reasoning text; AgentDiff compares steps, it does not interpret them."),
    "3.2": ("judge",
            "Weak verification means a check happened and was inadequate. AgentDiff "
            "can count verification-shaped steps but cannot grade one, and counting "
            "a check as adequate because it exists is exactly the error the mode "
            "names."),
}

_TRAIL_UNREACHABLE: dict[str, tuple[str, str]] = {
    "reasoning.output_generation.instruction_non_compliance": (
        "judge",
        "Requires reading the instruction and deciding the output disobeyed it. "
        "AgentDiff scores answers by token overlap with an expected string, which is "
        "not compliance."),
    "system_execution.configuration.environment_setup_errors": (
        "outside_trace",
        "Setup happens before the first step is logged; nothing in the step list "
        "distinguishes a bad environment from a bad plan."),
    "system_execution.api_issues.rate_limiting": (
        "label_vocabulary",
        "The evidence is often right there in the observation text, but AgentDiff "
        "collapses every error observation into one boolean (swallowed_error) and "
        "the HTTP status is discarded before any label is produced."),
    "system_execution.api_issues.authentication_errors": (
        "label_vocabulary",
        "Same collapse: an auth failure and a 500 raise the identical flag."),
    "system_execution.api_issues.resource_not_found": (
        "label_vocabulary",
        "Same collapse; 'not found' is one of the error markers matched but never "
        "surfaced as its own label."),
    "system_execution.resource_management.timeout_issues": (
        "label_vocabulary",
        "Per-step latency is recorded, but no threshold distinguishes a slow step "
        "from a timed-out one without knowing the harness's limit."),
    "planning.task_management.goal_deviation": (
        "judge",
        "The same judgement MAST 2.3 needs, under a different name: deviation is "
        "relative to a goal, and AgentDiff has no representation of the goal beyond "
        "the prompt string."),
}


def _reachable_codes(taxonomy: str) -> set[str]:
    """Codes with at least one ``direct`` or ``partial`` mapping.

    A ``none`` entry does not count as reach: it names a code precisely to
    record that AgentDiff cannot license it.
    """
    return {
        m.code for m in MAPPINGS
        if m.taxonomy == taxonomy and m.confidence in ("direct", "partial")
    }


def _fraction(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def coverage() -> dict:
    """What fraction of each taxonomy AgentDiff can reach at all, and why not.

    This is the honest headline of the module.  A mapping table on its own
    invites the reading "AgentDiff detects MAST failures"; the coverage
    numbers say how much of MAST it can see (a minority), which parts are
    dark (all of category 2, all of weak verification), and whether the
    darkness is fixable by richer traces or needs a judge.

    Reach is counted per *code*, not per detection: a code is reachable when
    some AgentDiff signal maps onto it with ``direct`` or ``partial``
    confidence.  Codes named only by a ``none`` entry are unreachable — the
    entry exists to record the refusal, not to claim the code.
    """
    mast_reached = _reachable_codes("MAST")
    trail_reached = _reachable_codes("TRAIL")

    mast_by_category: dict[str, dict] = {}
    for mode in MAST_MODES:
        bucket = mast_by_category.setdefault(
            mode.category, {"total": 0, "reachable": 0, "codes": [], "unreachable": []})
        bucket["total"] += 1
        if mode.code in mast_reached:
            bucket["reachable"] += 1
            bucket["codes"].append(mode.code)
        else:
            bucket["unreachable"].append(mode.code)
    for name, bucket in mast_by_category.items():
        bucket["fraction"] = _fraction(bucket["reachable"], bucket["total"])
        bucket["published_mass"] = PROVENANCE["MAST"]["category_mass"].get(name)

    trail_by_branch: dict[str, dict] = {}
    for leaf in TRAIL_LEAVES:
        bucket = trail_by_branch.setdefault(
            leaf.branch, {"total": 0, "reachable": 0, "codes": [], "unreachable": []})
        bucket["total"] += 1
        if leaf.code in trail_reached:
            bucket["reachable"] += 1
            bucket["codes"].append(leaf.code)
        else:
            bucket["unreachable"].append(leaf.code)
    for bucket in trail_by_branch.values():
        bucket["fraction"] = _fraction(bucket["reachable"], bucket["total"])

    mast_unreachable = [
        {
            "code": mode.code,
            "name": mode.name,
            "category": mode.category,
            "blocker": _MAST_UNREACHABLE[mode.code][0],
            "reason": _MAST_UNREACHABLE[mode.code][1],
        }
        for mode in MAST_MODES if mode.code not in mast_reached
    ]
    trail_unreachable = [
        {
            "code": leaf.code,
            "name": leaf.name,
            "branch": leaf.branch,
            "blocker": _TRAIL_UNREACHABLE[leaf.code][0],
            "reason": _TRAIL_UNREACHABLE[leaf.code][1],
        }
        for leaf in TRAIL_LEAVES if leaf.code not in trail_reached
    ]

    # Mass-weighted reach: how much of the *observed failure distribution*
    # each taxonomy's reachable part accounts for.  A count of modes flatters
    # AgentDiff, because the modes it misses are not rare ones.
    mast_mass = round(sum(
        bucket["published_mass"] * bucket["fraction"]
        for bucket in mast_by_category.values()
        if bucket["published_mass"] is not None
    ), 4)

    return {
        "provenance": PROVENANCE,
        "mast": {
            "total": len(MAST_MODES),
            "reachable": len(mast_reached),
            "fraction": _fraction(len(mast_reached), len(MAST_MODES)),
            "reachable_codes": sorted(mast_reached),
            "by_category": mast_by_category,
            "unreachable": mast_unreachable,
            "category_mass_weighted_reach": mast_mass,
            "note": (
                "Mode count flatters the tool. Weighted by MAST's published category "
                "mass, AgentDiff's reachable modes cover about "
                f"{mast_mass:.0%} of observed failures."
            ),
        },
        "trail": {
            "total": len(TRAIL_LEAVES),
            "reachable": len(trail_reached),
            "fraction": _fraction(len(trail_reached), len(TRAIL_LEAVES)),
            "reachable_codes": sorted(trail_reached),
            "by_branch": trail_by_branch,
            "unreachable": trail_unreachable,
        },
        "blockers": BLOCKERS,
        "blocker_counts": _blocker_counts(mast_unreachable, trail_unreachable),
        "caveats": list(CAVEATS),
        "narrative": _coverage_narrative(
            mast_reached, trail_reached, mast_by_category, trail_by_branch, mast_mass),
    }


def _blocker_counts(mast_unreachable: list[dict], trail_unreachable: list[dict]) -> dict:
    counts: dict[str, int] = {name: 0 for name in BLOCKERS}
    for row in list(mast_unreachable) + list(trail_unreachable):
        counts[row["blocker"]] += 1
    return counts


def _coverage_narrative(mast_reached, trail_reached, by_category, by_branch, mast_mass) -> str:
    inter = by_category[_INTER]
    system = by_branch[_SYSTEM]
    return (
        f"AgentDiff's deterministic signals reach {len(mast_reached)} of "
        f"{len(MAST_MODES)} MAST modes "
        f"({_fraction(len(mast_reached), len(MAST_MODES)):.0%}) and "
        f"{len(trail_reached)} of {len(TRAIL_LEAVES)} TRAIL leaves "
        f"({_fraction(len(trail_reached), len(TRAIL_LEAVES)):.0%}). "
        f"Weighted by MAST's published category distribution rather than by mode "
        f"count, the reachable part covers about {mast_mass:.0%} of observed "
        f"failures — the modes it misses are not the rare ones. "
        f"MAST's Inter-Agent Misalignment category is {inter['reachable']} of "
        f"{inter['total']} reachable: AgentDiff compares single-agent trajectories, "
        f"and {PROVENANCE['MAST']['category_mass'][_INTER]:.1%} of MAST's measured "
        f"failure mass is therefore structurally invisible to it, not merely "
        f"unmapped. On the TRAIL side, System Execution Errors is "
        f"{system['reachable']} of {system['total']}: the trace usually contains the "
        f"error text, but AgentDiff's vocabulary collapses every error into one "
        f"boolean and the status code never reaches a label. "
        + INCOMPLETENESS_CAVEAT + " " + METHOD_CAVEAT
    )


# --------------------------------------------------------------------------
# Standing caveats.
# --------------------------------------------------------------------------

INCOMPLETENESS_CAVEAT = (
    "Neither taxonomy is a superset of the other. MAST has no category for "
    "system-execution failures — rate limits, 5xx responses, auth errors, timeouts "
    "— which TRAIL spends 8 of its 20 leaves on; TRAIL has no verification category "
    "for the 21.3% of failures MAST attributes to weak (3.2) or absent (3.3) "
    "verification. Each is blind to roughly a fifth of the failure mass the other "
    "captures, so both are reported here and a reader who quotes only one is "
    "quoting an incomplete picture."
)

METHOD_CAVEAT = (
    "These labels are rule-based mappings of deterministic trace signals, not "
    "taxonomy labels. MAST's distribution was produced by 6 expert annotators "
    "(Cohen's kappa = 0.88) and an LLM judge validated at 94% accuracy / kappa = "
    "0.77 (arXiv 2503.13657); TRAIL's by expert annotation (arXiv 2505.08638). "
    "Nothing here reads a trace for meaning. The vocabulary is comparable; the "
    "method is not, and these counts must not be reported as a MAST or TRAIL "
    "measurement."
)

SINGLE_AGENT_CAVEAT = (
    "AgentDiff is a single-agent comparison tool. MAST's entire Inter-Agent "
    "Misalignment category (~36.9% of its measured mass) needs two or more agents "
    "exchanging messages and is reported as unreachable rather than force-fitted "
    "onto single-agent signals."
)

FAULT_CAVEAT = (
    "Neither taxonomy models fault attribution. Only tau-bench / tau2-bench treat "
    "who is at fault — user simulator, agent, or environment — as an orthogonal "
    "dimension; MAST and TRAIL fold it into the category, so neither can express "
    "'the agent was right and the user simulator was broken'. AgentDiff's own "
    "failed_agent field names which of two compared runs failed, which is a "
    "different question and does not fill the gap."
)

CAVEATS: tuple[str, ...] = (
    INCOMPLETENESS_CAVEAT,
    METHOD_CAVEAT,
    SINGLE_AGENT_CAVEAT,
    FAULT_CAVEAT,
)


# --------------------------------------------------------------------------
# Classifying a real comparison report.
# --------------------------------------------------------------------------

#: Process flags that need information a comparison report does not serialise.
#: ``report.json`` carries agent, outcome, totals and steps per side — but not
#: the offered tool table or the harness budget — so these four flags cannot
#: fire when the flags are recovered from a report.  Reported as unmeasurable
#: rather than as absent, because "no schema violations" and "we never checked"
#: are not the same claim.
_FLAGS_NEEDING_TOOLS = ("false_success", "undeclared_tools", "schema_violation")
_FLAGS_NEEDING_BUDGET = ("budget_pressure",)


def _trajectory_from_side(report: dict, side: str) -> Optional[Trajectory]:
    """Rebuild one side of a comparison report into a Trajectory, or None.

    A report stores each side as agent/outcome/totals/steps with the task
    hoisted to the top level, so the task is put back before validating.
    Returns None rather than raising: a report shape this module does not
    recognise should degrade to "process flags unavailable", not crash a
    caller who only wanted the divergence labels.
    """
    payload = report.get(side)
    if not isinstance(payload, dict) or not payload.get("steps"):
        return None
    data = dict(payload)
    data.setdefault("task", report.get("task") or {"id": "unknown", "prompt": ""})
    try:
        return Trajectory.from_dict(data)
    except (ValueError, TypeError, KeyError):
        return None


def process_flags(report: dict) -> dict:
    """The process pathology flags raised by each side of a report.

    Prefers a ``process`` block if the report already carries one; otherwise
    recomputes from the serialised steps.  Either way the result records
    which flags were *unmeasurable* on this input, because a flag that could
    not fire is not evidence of a clean run.
    """
    block = report.get("process")
    if isinstance(block, dict) and isinstance(block.get("a"), dict):
        raised = {
            side: sorted(block.get(side, {}).get("gap", {}).get("raised", []) or [])
            for side in ("a", "b")
        }
        return {"available": True, "basis": "report's own process block",
                "raised": raised, "unmeasurable": []}

    from . import process as process_module  # local: keeps import cost off the table

    raised: dict[str, list[str]] = {}
    unmeasurable: set[str] = set()
    available = False
    for side in ("a", "b"):
        trajectory = _trajectory_from_side(report, side)
        if trajectory is None:
            raised[side] = []
            continue
        available = True
        analysis = process_module.analyse(trajectory)
        raised[side] = sorted(analysis["gap"]["raised"])
        if not trajectory.tools:
            unmeasurable.update(_FLAGS_NEEDING_TOOLS)
        if not trajectory.budget.get("max_steps"):
            unmeasurable.update(_FLAGS_NEEDING_BUDGET)
    return {
        "available": available,
        "basis": (
            "recomputed from the report's serialised steps"
            if available else "no usable trajectory in the report"
        ),
        "raised": raised,
        "unmeasurable": sorted(unmeasurable),
        "unmeasurable_note": (
            "A comparison report does not serialise the offered tool table or the "
            "harness budget, so these flags could not fire on this input. Their "
            "absence is unchecked, not clean."
        ) if unmeasurable else None,
    }


def _label(mapping: Mapping, occurrences: int, detail: list[str]) -> dict:
    entry = MAST_BY_CODE[mapping.code] if mapping.taxonomy == "MAST" else TRAIL_BY_CODE[mapping.code]
    label = {
        "taxonomy": mapping.taxonomy,
        "code": mapping.code,
        "name": entry.name,
        "confidence": mapping.confidence,
        "from_signal": mapping.signal,
        "source": mapping.source,
        "occurrences": occurrences,
        "why": mapping.why,
    }
    if mapping.taxonomy == "MAST":
        label["category"] = entry.category
    else:
        label["branch"] = entry.branch
        label["group"] = entry.group
    if detail:
        label["evidence"] = detail
    return label


def classify(report: dict) -> dict:
    """Taxonomy labels implied by one comparison report's divergences and flags.

    Takes the shape ``deepcompare batch`` writes (SCHEMA.md: ``task``, ``a``,
    ``b``, ``alignment``, ``divergences``, ``attribution``, ...) and returns
    the MAST modes and TRAIL leaves its signals license, with counts per MAST
    category and TRAIL branch.

    Two things this deliberately does not do.  It does not pick a winning
    taxonomy: both are emitted, because each is blind to a fifth of what the
    other sees.  And it does not promote a mapping's confidence based on how
    many times a signal fired — twelve partial matches are still partial, and
    a count is not evidence about a definition.

    Signals whose only mapping is ``none`` appear under ``declined`` with the
    reason, so a reader can see what was detected and deliberately not
    labelled.  Silence there would look like the signal never fired.
    """
    divergences = report.get("divergences") or []
    kinds: dict[str, int] = {}
    kind_detail: dict[str, list[str]] = {}
    for divergence in divergences:
        kind = divergence.get("kind")
        if kind is None:
            continue
        kinds[kind] = kinds.get(kind, 0) + 1
        kind_detail.setdefault(kind, []).append(
            f"divergence #{divergence.get('rank')}: {divergence.get('summary', '')}"[:200]
        )

    flags = process_flags(report)
    flag_counts: dict[str, int] = {}
    flag_detail: dict[str, list[str]] = {}
    for side in ("a", "b"):
        agent = (report.get(side) or {}).get("agent", {}).get("name", side)
        for flag in flags["raised"].get(side, []):
            flag_counts[flag] = flag_counts.get(flag, 0) + 1
            flag_detail.setdefault(flag, []).append(f"{agent} ({side})")

    signals = dict(kinds)
    for flag, count in flag_counts.items():
        signals[flag] = signals.get(flag, 0) + count
    detail = {**kind_detail, **flag_detail}

    labels: dict[str, list[dict]] = {"MAST": [], "TRAIL": []}
    declined: list[dict] = []
    seen: dict[tuple[str, str, str], dict] = {}
    for signal in sorted(signals, key=lambda s: (
            (DIVERGENCE_KINDS + PROCESS_FLAGS).index(s)
            if s in DIVERGENCE_KINDS + PROCESS_FLAGS else 999)):
        count = signals[signal]
        for mapping in mappings_for(signal):
            if mapping.confidence == "none":
                declined.append({
                    "signal": signal,
                    "taxonomy": mapping.taxonomy,
                    "nearest_code": mapping.code,
                    "occurrences": count,
                    "reason": mapping.why,
                })
                continue
            key = (mapping.taxonomy, mapping.code, mapping.signal)
            if key in seen:
                seen[key]["occurrences"] += count
                continue
            label = _label(mapping, count, detail.get(signal, [])[:4])
            seen[key] = label
            labels[mapping.taxonomy].append(label)

    mast_by_category = _counts(labels["MAST"], "category",
                               [mode.category for mode in MAST_MODES])
    trail_by_branch = _counts(labels["TRAIL"], "branch",
                              [leaf.branch for leaf in TRAIL_LEAVES])

    result = {
        "task": (report.get("task") or {}).get("id"),
        "agents": {
            side: (report.get(side) or {}).get("agent", {}).get("name")
            for side in ("a", "b")
        },
        "signals": {
            "divergence_kinds": dict(sorted(kinds.items())),
            "process_flags": {
                side: flags["raised"].get(side, []) for side in ("a", "b")
            },
            "process_basis": flags["basis"],
            "process_unmeasurable": flags.get("unmeasurable", []),
        },
        "mast": {
            "labels": labels["MAST"],
            "by_category": mast_by_category,
            "by_code": _counts(labels["MAST"], "code",
                               [mode.code for mode in MAST_MODES]),
            "distinct_modes": len({label["code"] for label in labels["MAST"]}),
        },
        "trail": {
            "labels": labels["TRAIL"],
            "by_branch": trail_by_branch,
            "by_code": _counts(labels["TRAIL"], "code",
                               [leaf.code for leaf in TRAIL_LEAVES]),
            "distinct_leaves": len({label["code"] for label in labels["TRAIL"]}),
        },
        "declined": declined,
        "caveats": list(CAVEATS),
        "provenance": PROVENANCE,
    }
    result["narrative"] = narrative(result)
    return result


def _counts(labels: list[dict], key: str, universe: list[str]) -> dict:
    """Occurrence counts keyed by ``key``, over the full universe of values.

    Every category is present, including the zeroes.  A missing key would
    read as "not applicable"; an explicit 0 reads as "looked, found none",
    which is the claim actually being made.
    """
    counts = {name: 0 for name in dict.fromkeys(universe)}
    for label in labels:
        value = label.get(key)
        if value is not None:
            counts[value] = counts.get(value, 0) + label["occurrences"]
    return counts


def classify_batch(reports: Iterable[dict]) -> dict:
    """Aggregate :func:`classify` over a batch, summing per category and code.

    A batch is the honest unit for a distribution: one report's counts are too
    small to compare against MAST's 41.8 / 36.9 / 21.3 split, and quoting them
    as though they were is the mistake this module exists to prevent.  Even at
    batch size the comparison stays qualitative — see the method caveat.
    """
    per_task, mast_cat, mast_code, trail_branch, trail_code = [], {}, {}, {}, {}
    signal_totals: dict[str, int] = {}
    for report in reports:
        result = classify(report)
        per_task.append(result)
        for target, source in (
            (mast_cat, result["mast"]["by_category"]),
            (mast_code, result["mast"]["by_code"]),
            (trail_branch, result["trail"]["by_branch"]),
            (trail_code, result["trail"]["by_code"]),
        ):
            for name, count in source.items():
                target[name] = target.get(name, 0) + count
        for name, count in result["signals"]["divergence_kinds"].items():
            signal_totals[name] = signal_totals.get(name, 0) + count
        for side in ("a", "b"):
            for flag in result["signals"]["process_flags"].get(side, []):
                signal_totals[flag] = signal_totals.get(flag, 0) + 1
    return {
        "tasks": len(per_task),
        "per_task": per_task,
        "signal_totals": dict(sorted(signal_totals.items())),
        "mast": {"by_category": mast_cat, "by_code": mast_code},
        "trail": {"by_branch": trail_branch, "by_code": trail_code},
        "coverage": coverage(),
        "caveats": list(CAVEATS),
    }


# --------------------------------------------------------------------------
# Narrative.
# --------------------------------------------------------------------------


def narrative(classification: dict) -> str:
    """Plain-language account of one classification, caveats included.

    The caveats are part of the sentence rather than a footnote on purpose.
    A taxonomy label is the kind of output that gets copied into a slide with
    its provenance left behind, and by the time it lands there "AgentDiff
    found 3 MAST 1.3s" is indistinguishable from an annotated measurement.
    Attaching the method caveat to the prose is the only place it cannot be
    detached from the number.
    """
    kinds = classification["signals"]["divergence_kinds"]
    flags = classification["signals"]["process_flags"]
    mast = classification["mast"]
    trail = classification["trail"]

    raised = sorted({flag for side in ("a", "b") for flag in flags.get(side, [])})
    if kinds:
        found = ", ".join(f"{count}x {kind}" for kind, count in sorted(kinds.items()))
        opening = f"This comparison diverged {sum(kinds.values())} time(s) ({found})"
    else:
        opening = "This comparison found no divergences"
    if raised:
        opening += f" and raised process flags: {', '.join(raised)}."
    else:
        opening += " and raised no process flags."

    if mast["distinct_modes"] or trail["distinct_leaves"]:
        top_cat = sorted(
            ((count, name) for name, count in mast["by_category"].items() if count),
            reverse=True)
        top_branch = sorted(
            ((count, name) for name, count in trail["by_branch"].items() if count),
            reverse=True)
        body = (
            f" Those signals map onto {mast['distinct_modes']} MAST mode(s)"
            + (f", concentrated in {top_cat[0][1]}" if top_cat else "")
            + f", and {trail['distinct_leaves']} TRAIL leaf/leaves"
            + (f", concentrated in {top_branch[0][1]}" if top_branch else "")
            + "."
        )
    else:
        body = " No taxonomy label is licensed by these signals."

    if classification["declined"]:
        declined = "; ".join(sorted({
            f"{row['signal']} -> {row['taxonomy']} {row['nearest_code']}"
            for row in classification["declined"]
        }))
        body += (
            f" {len(classification['declined'])} signal-to-code pairing(s) were "
            f"considered and declined ({declined}) — the nearest code exists but the "
            f"signal does not license it."
        )

    unmeasurable = classification["signals"].get("process_unmeasurable") or []
    if unmeasurable:
        body += (
            f" Note that {', '.join(unmeasurable)} could not be evaluated on this "
            f"input ({classification['signals']['process_basis']}), so their absence "
            f"is unchecked rather than clean."
        )

    return opening + body + " " + INCOMPLETENESS_CAVEAT + " " + METHOD_CAVEAT
