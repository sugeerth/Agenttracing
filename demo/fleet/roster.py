"""The 33-agent demo fleet roster.

Composition (story beats the fleet encodes):

- #1/#2 flagship pair: atlas-v2 / bolt-v3 — hand-authored traces reused
  verbatim from demo/traces so the flagship comparison stays consistent.
- Strong all-rounders (nova, vega, quasar, zephyr) — high success, but NOT
  uniformly best: quasar/nova are accurate-but-expensive, zephyr is
  fast-and-cheap with an occasional premature answer.
- Mid-tier agents with exactly one visible weakness each.
- Specialist/flawed variants covering every archetype at high intensity.
- Version pairs for regression stories:
    cedar-v1 -> cedar-v2  (sloppy_args flaw FIXED in v2: improvement)
    rondo-v1 -> rondo-v2  (v2 shipped an "efficiency mode" that answers
                           early: a genuine REGRESSION, v2 worse than v1)
- Clearly weak agents (koda, umbra, flora) anchoring the bottom of the
  success spread (~40%).
"""

from __future__ import annotations

from personas import Persona

ROSTER: list[Persona] = [
    # -- flagship pair (traces reused verbatim from demo/agents.py) --------
    Persona("atlas-v2", "claude-sonnet-5", "v2", "primary_source", 0.85, 0.93,
            "flagship A: efficient, authoritative sources; one scripted tool_execution failure (t07)",
            scripted=True),
    Persona("bolt-v3", "gpt-5", "v3", "blog_truster", 0.55, 0.85,
            "flagship B: starts like atlas, then trusts weak sources; scripted 5/8",
            scripted=True),

    # -- strong all-rounders ----------------------------------------------
    Persona("nova-v4", "claude-opus-5", "v4", "primary_source", 0.95, 0.97,
            "top accuracy; cross-checks everything, so priciest of the leaders"),
    Persona("vega-v2", "gemini-3-pro", "v2", "primary_source", 0.70, 0.93,
            "reliable and lean; occasional cross-check, few wasted steps"),
    Persona("quasar-v3", "gpt-5", "v3", "verbose_reasoner", 0.55, 0.95,
            "accurate but deliberates at length: high output tokens/latency"),
    Persona("zephyr-v5", "claude-sonnet-5", "v5", "early_stopper", 0.25, 0.94,
            "fast and cheap; skips verification, rarely burned by it"),

    # -- mid-tier, one weakness each --------------------------------------
    Persona("terra-v2", "gemini-3-pro", "v2", "blog_truster", 0.45, 0.86,
            "solid except a taste for secondary sources on lookup tasks"),
    Persona("orbit-v1", "gpt-5-mini", "v1", "over_searcher", 0.60, 0.86,
            "gets there, but re-searches what it already found"),
    Persona("juno-v2", "mistral-large-3", "v2", "sloppy_args", 0.45, 0.85,
            "right tools, careless arguments; usually catches its own errors"),
    Persona("onyx-v3", "llama-4-70b", "v3", "wrong_tool", 0.50, 0.82,
            "occasionally grabs the wrong tool on quantitative tasks"),
    Persona("willow-v2", "claude-sonnet-5", "v2", "no_planner", 0.50, 0.80,
            "no upfront plan; meanders early, usually converges"),
    Persona("mica-v1", "gemini-3-flash", "v1", "early_stopper", 0.50, 0.84,
            "quick answers from partial evidence; cheap, mixed accuracy"),
    Persona("sable-v2", "gpt-5-mini", "v2", "verbose_reasoner", 0.70, 0.86,
            "wordy deliberation on a budget model; okay accuracy, high tokens"),
    Persona("raven-v3", "mistral-large-3", "v3", "primary_source", 0.50, 0.75,
            "unremarkable but disciplined; middle of every pack"),

    # -- specialists / flawed variants across all archetypes ---------------
    Persona("comet-v1", "gemini-3-flash", "v1", "over_searcher", 0.90, 0.80,
            "search addict: floods every task with redundant queries"),
    Persona("gecko-v2", "llama-4-70b", "v2", "blog_truster", 0.70, 0.76,
            "forum-and-blog diet; frequently cites the wrong number"),
    Persona("pixel-v1", "gpt-5-mini", "v1", "sloppy_args", 0.75, 0.73,
            "chronic argument typos: wrong units, wrong offsets, greedy regexes"),
    Persona("tango-v2", "mistral-large-3", "v2", "wrong_tool", 0.70, 0.75,
            "wrong tool for the job on most quantitative tasks"),
    Persona("luna-v1", "gemini-3-flash", "v1", "early_stopper", 0.80, 0.71,
            "speed demon: cheapest traces in the fleet, pays for it in accuracy"),
    Persona("nimbus-v2", "llama-4-70b", "v2", "no_planner", 0.80, 0.68,
            "starts typing before thinking; long meanders, some lucky finishes"),
    Persona("otter-v1", "gpt-5-mini", "v1", "verbose_reasoner", 0.90, 0.79,
            "rambles: triple-length reasoning on every task"),
    Persona("halo-v2", "claude-sonnet-5", "v2", "blog_truster", 0.55, 0.81,
            "decent instincts undermined by aggregator trust"),
    Persona("delta-v2", "gemini-3-pro", "v2", "wrong_tool", 0.65, 0.77,
            "misapplies tools under pressure; recovers about half the time"),
    Persona("sonar-v1", "llama-4-70b", "v1", "over_searcher", 0.75, 0.77,
            "echo-searches: keeps confirming what it already knows"),

    # -- version pairs (regression-story material) --------------------------
    Persona("cedar-v1", "claude-sonnet-5", "v1", "sloppy_args", 0.70, 0.81,
            "v1: notorious for malformed tool args (monthly-vs-annual, bad offsets)"),
    Persona("cedar-v2", "claude-sonnet-5", "v2", "sloppy_args", 0.05, 0.89,
            "v2: args templater shipped — the sloppy_args flaw is fixed (improvement pair)"),
    Persona("rondo-v1", "gpt-5", "v1", "early_stopper", 0.10, 0.90,
            "v1: patient, verifies before answering; strong baseline"),
    Persona("rondo-v2", "gpt-5", "v2", "early_stopper", 0.70, 0.90,
            "v2: new 'efficiency mode' answers early — a real regression vs v1"),

    # -- remaining specialists ---------------------------------------------
    Persona("ember-v1", "mistral-large-3", "v1", "no_planner", 0.65, 0.66,
            "plunges in unplanned; wanders on hard tasks"),
    Persona("iris-v2", "gemini-3-pro", "v2", "verbose_reasoner", 0.40, 0.90,
            "measured, slightly wordy, dependable — second accurate-but-expensive"),

    # -- clearly weak agents -------------------------------------------------
    Persona("koda-v1", "llama-4-70b", "v1", "blog_truster", 0.90, 0.58,
            "weak: believes whatever ranks third; bottom-quartile accuracy"),
    Persona("umbra-v1", "gemini-3-flash", "v1", "wrong_tool", 0.90, 0.45,
            "weak: wrong tool almost every time it matters"),
    Persona("flora-v1", "gpt-5-mini", "v1", "early_stopper", 0.95, 0.52,
            "weakest: answers from headlines; the fleet's trap agent"),
]

assert len(ROSTER) == 33, f"roster must have exactly 33 agents, has {len(ROSTER)}"
assert len({p.name for p in ROSTER}) == 33, "agent names must be distinct"

ROSTER_BY_NAME = {p.name: p for p in ROSTER}
