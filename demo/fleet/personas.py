"""Parametric persona model for the DeepCompare demo fleet.

A persona is a behavioral archetype plus tunable parameters:

- ``archetype``   one of the eight behavior families below
- ``intensity``   0..1 — how strongly the archetype's behavior expresses
  (0 = indistinguishable from the canonical good path, 1 = maximal)
- ``competence``  0..1 — base skill; drives a small generic slip rate on
  hard tasks independent of the archetype
- ``model`` / ``version``  labels for the trace agent block

Archetypes
----------
primary_source    Prefers official/primary sources; adds occasional
                  cross-check steps. Positive archetype: intensity buys
                  reliability at a small token cost.
blog_truster      Retrieval quality drops with intensity: picks blogs,
                  forums, and aggregators over primary sources.
over_searcher     Issues redundant searches and confirmation reads before
                  committing; rarely fatal, always more steps/tokens.
wrong_tool        Reaches for an inappropriate tool on tool-heavy tasks
                  (plain calculator for timezone math, web search for
                  arithmetic, naive grep for log triage).
sloppy_args       Picks the right tool but malforms the arguments (monthly
                  vs annual prices, wrong UTC offsets, greedy regexes).
no_planner        Skips the plan step and meanders through ad-hoc
                  reasoning before settling into the task.
early_stopper     Answers before gathering enough evidence; fast and cheap
                  when it gets lucky, wrong when the shortcut mattered.
verbose_reasoner  Interleaves long deliberation steps; accurate but heavy
                  on output tokens and latency.
"""

from __future__ import annotations

from dataclasses import dataclass

ARCHETYPES = {
    "primary_source": "prefers official sources, cross-checks before answering",
    "blog_truster": "retrieval quality drops with intensity (blogs/forums/aggregators)",
    "over_searcher": "redundant searches and confirmation reads; slow, rarely wrong",
    "wrong_tool": "picks an inappropriate tool on tool-heavy tasks",
    "sloppy_args": "right tool, malformed arguments",
    "no_planner": "skips planning; ad-hoc meandering reasoning",
    "early_stopper": "answers before enough evidence; fast but risky",
    "verbose_reasoner": "extra deliberation steps; accurate but token-heavy",
}


@dataclass(frozen=True)
class Persona:
    """One fleet member: identity plus behavior parameters."""

    name: str          # distinct memorable name, e.g. "nova-v4"
    model: str         # model label, e.g. "claude-opus-5"
    version: str       # e.g. "v4" (matches the name suffix)
    archetype: str     # key into ARCHETYPES
    intensity: float   # 0..1
    competence: float  # 0..1
    blurb: str         # one-line expected profile for ROSTER.md
    scripted: bool = False  # True for the hand-authored flagship pair

    def __post_init__(self):
        if self.archetype not in ARCHETYPES:
            raise ValueError(f"{self.name}: unknown archetype {self.archetype!r}")
        if not 0.0 <= self.intensity <= 1.0:
            raise ValueError(f"{self.name}: intensity out of range")
        if not 0.0 <= self.competence <= 1.0:
            raise ValueError(f"{self.name}: competence out of range")

    @property
    def agent_info(self) -> dict:
        return {"name": self.name, "model": self.model, "version": self.version}
