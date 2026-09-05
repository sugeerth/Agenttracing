"""Generate the 33-agent demo fleet: 264 trajectories on the 8 demo tasks.

Run from the repo root:

    python demo/fleet/generate_fleet.py

- Reuses demo/tasks.py and demo/simulator.py (repo-root-relative sys.path).
- Members #1/#2 (atlas-v2, bolt-v3) reuse the hand-authored trajectories
  from demo/agents.py verbatim, so the flagship pair stays byte-consistent
  with demo/traces/.
- The other 31 agents are composed programmatically: canonical good-path
  skeletons (extracted from the flagship traces) plus archetype-specific
  divergences injected with intensity-scaled frequency and severity, with
  wording varied through seeded phrase banks.
- Every trace is validated with deepcompare.trace.Trajectory.from_json and
  checked for canonical common-prefix wording before the first divergence.

Deterministic (constant seeds derived from agent/task ids, no wall clock),
idempotent, stdlib only.
"""

from __future__ import annotations

import random
import sys

from pathlib import Path

_HERE = Path(__file__).resolve().parent      # demo/fleet
_REPO = _HERE.parent.parent                  # repo root
for _p in (str(_REPO), str(_REPO / "demo"), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tasks import TASKS  # noqa: E402  (demo/tasks.py)
from simulator import TrajectoryBuilder, write_trajectory  # noqa: E402
import agents as flagship_agents  # noqa: E402  (demo/agents.py)
from personas import Persona  # noqa: E402
from roster import ROSTER  # noqa: E402
from deepcompare.trace import Trajectory  # noqa: E402

TRACES_DIR = _HERE / "traces"
ROSTER_MD = _HERE / "ROSTER.md"


# ===========================================================================
# Canonical skeletons: the good path per task, taken from the flagship
# traces (atlas-v2 everywhere except t07, where atlas fails by script and
# bolt-v3 walks the correct path).
# ===========================================================================

def _build_canon() -> tuple[dict[str, list[dict]], list[dict]]:
    flagship = flagship_agents.build_all()
    canon: dict[str, list[dict]] = {}
    for tr in flagship:
        tid, name = tr["task"]["id"], tr["agent"]["name"]
        use = (name == "bolt-v3") if tid == "t07_build_failure" else (name == "atlas-v2")
        if use:
            canon[tid] = [
                {"type": s["type"], "name": s["name"], "input": s["input"], "output": s["output"]}
                for s in tr["steps"]
            ]
    return canon, flagship


CANON, FLAGSHIP_TRACES = _build_canon()

T01, T02, T03, T04 = "t01_acme_revenue", "t02_cve_libfoo", "t03_saas_pricing", "t04_rope_paper"
T05, T06, T07, T08 = "t05_flight_duration", "t06_bls_unemployment", "t07_build_failure", "t08_changelog_diff"


# ===========================================================================
# Hazard model: where each task is vulnerable, and how often/badly each
# archetype trips there.
# ===========================================================================

HAZ = {
    #      retrieval divergence idx | tool divergence idx | early-stop config | generic hardness
    T01: {"retrieval": 2, "tool": None, "early_fatal_after": 1, "early_skip": (), "hardness": 0.22},
    T02: {"retrieval": 2, "tool": None, "early_fatal_after": 2, "early_skip": (4,), "hardness": 0.25},
    T03: {"retrieval": None, "tool": 4, "early_fatal_after": None, "early_skip": (4,), "hardness": 0.20},
    T04: {"retrieval": 2, "tool": None, "early_fatal_after": None, "early_skip": (), "hardness": 0.10},
    T05: {"retrieval": None, "tool": 1, "early_fatal_after": None, "early_skip": (3, 4), "hardness": 0.42},
    T06: {"retrieval": 2, "tool": None, "early_fatal_after": 1, "early_skip": (), "hardness": 0.30},
    T07: {"retrieval": None, "tool": 3, "early_fatal_after": 1, "early_skip": (), "hardness": 0.48},
    T08: {"retrieval": None, "tool": None, "early_fatal_after": 1, "early_skip": (4,), "hardness": 0.20},
}

# P(archetype flaw expresses on task) = intensity * SUSCEPT[archetype][task]
SUSCEPT = {
    "blog_truster": {T01: 0.95, T02: 0.80, T04: 0.60, T06: 0.90},
    "wrong_tool": {T03: 0.60, T05: 0.90, T07: 0.85},
    "sloppy_args": {T03: 0.70, T05: 0.80, T07: 0.90},
    "early_stopper": {T01: 0.70, T02: 0.60, T03: 0.50, T05: 0.50, T06: 0.80, T07: 0.70, T08: 0.85},
}

# P(flaw is fatal | flaw expressed) = FATALITY * (0.4 + 0.8 * intensity)
FATALITY = {
    "blog_truster": {T01: 0.65, T02: 0.45, T04: 0.0, T06: 0.70},
    "wrong_tool": {T03: 0.35, T05: 0.80, T07: 0.70},
    "sloppy_args": {T03: 0.50, T05: 0.45, T07: 0.60},
    "early_stopper": {T01: 0.75, T02: 0.35, T03: 0.0, T05: 0.0, T06: 0.80, T07: 0.75, T08: 0.70},
}

# Which flaw a generic (competence) slip takes on each task, mapped to the
# archetype whose fatality table it borrows.
SLIP_KIND = {
    T01: ("retrieval", "blog_truster"),
    T02: ("retrieval", "blog_truster"),
    T03: ("tool_sloppy", "sloppy_args"),
    T04: ("retrieval", "blog_truster"),
    T05: ("tool_sloppy", "sloppy_args"),
    T06: ("retrieval", "blog_truster"),
    T07: ("tool_sloppy", "sloppy_args"),
    T08: ("early", "early_stopper"),
}


# ===========================================================================
# Phrase banks (seeded choices — varied wording, never lorem ipsum)
# ===========================================================================

# Correct final answers: variant 0 is the canonical flagship wording.
ANSWER_PARAPHRASES = {
    T01: [
        "ACME Corp reported total revenue of $4.82 billion for fiscal year "
        "2025 (up 11% year over year), per the FY2025 results release on "
        "ir.acmecorp.com.",
        "Per its investor-relations FY2025 results release, ACME Corp's "
        "fiscal 2025 total revenue was $4.82 billion, an 11% increase over "
        "the prior year.",
    ],
    T02: [
        "The first release fixing CVE-2025-1234 is libfoo 2.14.1: the GHSA "
        "advisory lists patched version 2.14.1 and the 2.14.1 changelog "
        "cites the CVE directly.",
        "libfoo 2.14.1. The advisory's affected range is >=2.10.0, <2.14.1, "
        "and the v2.14.1 release notes name CVE-2025-1234 explicitly.",
    ],
    T03: [
        "Team wins on price for 25 annual seats: $11,700/year versus Pro's "
        "$14,700/year — a $3,000 annual difference that on Pro buys SSO, "
        "audit logs, and longer history.",
        "At 25 seats billed annually, Team costs $11,700 per year and Pro "
        "$14,700, so Team is cheaper by $3,000 per year.",
    ],
    T04: [
        "RoPE was introduced in \"RoFormer: Enhanced Transformer with "
        "Rotary Position Embedding\" (Jianlin Su et al.), first posted to "
        "arXiv in 2021 as 2104.09864.",
        "The introducing paper is \"RoFormer: Enhanced Transformer with "
        "Rotary Position Embedding\" by Jianlin Su and coauthors, 2021 "
        "(arXiv:2104.09864).",
    ],
    T05: [
        "Total elapsed time is 23 hours 45 minutes — 13h40m SIN-LHR, a "
        "2h15m Heathrow connection, and 7h50m LHR-JFK, all computed in UTC.",
        "23 hours 45 minutes door to door: legs of 13h40m and 7h50m with a "
        "2h15m connection, confirmed by the end-to-end UTC difference.",
    ],
    T06: [
        "The December 2024 unemployment rate was 4.1 percent, per the BLS "
        "Employment Situation release published January 10, 2025.",
        "4.1 percent — the December 2024 figure in the BLS Employment "
        "Situation news release.",
    ],
    T07: [
        "The failure traces to PR #482's pydantic 2.7.1 -> 3.0.0 bump: "
        "pydantic 3 removed the v1-style `validator` import, so test "
        "collection dies with ImportError. Migrate to `@field_validator` "
        "or pin pydantic<3.",
        "Root cause: pydantic 3.0.0 (bumped in PR #482) no longer exports "
        "`validator`, which tests/test_models.py imports — hence the "
        "collection ImportError. Move to `@field_validator` or pin "
        "pydantic==2.7.1.",
    ],
    T08: [
        "4.2 -> 5.0 has three breaking changes: Cache() now defaults to "
        "ttl=300s (was never-expire), purge() is gone in favor of clear(), "
        "and Python 3.8/3.9 support was dropped (>=3.10 required).",
        "Three breaking changes land in fastcache 5.0: a 300-second default "
        "TTL replacing never-expire, removal of cache.purge() (use "
        "clear()), and a new Python >=3.10 floor.",
    ],
}

REDUNDANT_SEARCHES = {
    T01: [
        ("ACME Corp annual report 2025 pdf",
         "[1] ir.acmecorp.com — Annual Report 2025 (PDF) | [2] "
         "annualreports.com — ACME Corp | [3] sec.gov — ACME Corp filings"),
        ("ACME fiscal 2025 revenue press release",
         "[1] ir.acmecorp.com — FY2025 results | [2] prnewswire.com — ACME "
         "Reports Q4 and FY2025 | [3] marketpulse.io — coverage roundup"),
        ("ACME Corp 10-K 2025 total revenue",
         "[1] sec.gov — ACME Corp Form 10-K | [2] ir.acmecorp.com — SEC "
         "filings | [3] stockanalysis-style summary — ACME financials"),
    ],
    T02: [
        ("libfoo CVE-2025-1234 patched version",
         "[1] nvd.nist.gov — CVE-2025-1234 Detail | [2] github.com — "
         "GHSA-6q9p-h4mw-2xr7 | [3] osv.dev — GHSA-6q9p-h4mw-2xr7"),
        ("libfoo TLV parser overflow security advisory",
         "[1] github.com — GHSA-6q9p-h4mw-2xr7 | [2] nvd.nist.gov — "
         "CVE-2025-1234 | [3] lwn-style writeup — libfoo overflow analysis"),
        ("libfoo 2.14 release security fix",
         "[1] github.com/libfoo/libfoo/releases — v2.14.1 | [2] "
         "forum.libfoo.org — release discussion | [3] repology — libfoo"),
    ],
    T03: [
        ("DataHub Cloud Team plan price per user 2025",
         "[1] datahubcloud.com/pricing — Plans & Pricing | [2] "
         "saasradar.com — DataHub Cloud plans compared | [3] listing site — "
         "DataHub Cloud pricing overview"),
        ("DataHub Cloud Pro plan annual billing discount",
         "[1] datahubcloud.com/pricing — Plans & Pricing | [2] "
         "datahubcloud.com/blog — Annual billing update | [3] saasradar.com "
         "— plans compared"),
        ("DataHub Cloud pricing 25 seats",
         "[1] datahubcloud.com/pricing — Plans & Pricing | [2] reddit.com — "
         "r/dataeng pricing thread | [3] saasradar.com — comparison"),
    ],
    T04: [
        ("RoFormer paper year arxiv",
         "[1] arxiv.org/abs/2104.09864 — RoFormer | [2] semanticscholar — "
         "RoFormer | [3] paperswithcode.com — RoPE"),
        ("who introduced rotary position embedding",
         "[1] arxiv.org/abs/2104.09864 — RoFormer | [2] blog.eleuther.ai — "
         "Rotary Embeddings | [3] survey — positional encodings overview"),
        ("rotary position embedding first paper 2021",
         "[1] arxiv.org/abs/2104.09864 — RoFormer | [2] paperswithcode.com "
         "— Rotary Position Embedding | [3] openreview mirror"),
    ],
    T05: [
        ("SQ306 Singapore London flight time",
         "[1] flighttrack-style page — SQ306 SIN-LHR, typically ~13h50m | "
         "[2] singaporeair.com — SQ306 schedule | [3] airport timetable"),
        ("BA117 London New York duration",
         "[1] flighttrack-style page — BA117 LHR-JFK, typically ~8h | [2] "
         "britishairways.com — BA117 schedule | [3] airport timetable"),
        ("Singapore London New York total travel time one stop",
         "[1] travel forum — SIN-LHR-JFK itineraries | [2] flight search — "
         "one-stop options ~24h | [3] travel blog — long-haul tips"),
    ],
    T06: [
        ("BLS Employment Situation December 2024 release date",
         "[1] bls.gov — Employment Situation archives | [2] bls.gov — "
         "release calendar (Jan 10, 2025) | [3] econwire.com — preview"),
        ("unemployment rate December 2024 bls.gov",
         "[1] bls.gov/news.release/archives/empsit_01102025.htm | [2] "
         "bls.gov/cps — CPS home | [3] tradingecon.com — US unemployment"),
        ("US jobless rate December 2024 official",
         "[1] bls.gov — Employment Situation — December 2024 | [2] "
         "econwire.com — jobs report coverage | [3] fred-style chart — UNRATE"),
    ],
    T07: [
        ("pydantic 3.0 breaking changes validator",
         "[1] docs.pydantic.dev — Migration guide | [2] github.com — "
         "pydantic 3.0.0 release notes | [3] stackoverflow — import "
         "'validator' fails after upgrade"),
        ("widget-service CI failure pytest collection ImportError",
         "[1] internal wiki — CI runbook | [2] stackoverflow — pytest "
         "'errors during collection' | [3] github.com — pytest issue tracker"),
        ("fastapi 0.115 pydantic compatibility",
         "[1] github.com — fastapi release notes | [2] docs.pydantic.dev — "
         "version policy | [3] forum thread — fastapi/pydantic matrix"),
    ],
    T08: [
        ("fastcache 5.0 release notes",
         "[1] github.com/fastcache/fastcache/releases — v5.0.0 | [2] "
         "fastcache.dev/changelog — Changelog | [3] pypi.org — fastcache"),
        ("fastcache purge removed clear",
         "[1] stackoverflow — fastcache 5.0 purge() gone? | [2] "
         "github.com — fastcache #1201: rename purge to clear | [3] "
         "fastcache.dev/changelog"),
        ("fastcache 5.0 python version requirement",
         "[1] pypi.org — fastcache (requires-python >=3.10) | [2] "
         "github.com — v5.0.0 notes | [3] fastcache.dev/changelog"),
    ],
}

REDUNDANT_NOTES = [
    "Redundant: this query re-finds what the previous search already surfaced.",
    "Redundant confirmation query; no new information gained.",
    "Echo search — the needed source was already in hand.",
]

MEANDER = {
    T01: [("Getting oriented.",
           "No plan yet — I'll just start pulling whatever mentions ACME's "
           "2025 numbers and see which figures repeat across sources."),
          ("Where to begin?",
           "Skipping the planning step; revenue figures usually surface "
           "fast, so I'll search first and sort sources out as I go.")],
    T02: [("Getting oriented.",
           "Not going to plan this out — CVE lookups are routine. Query "
           "first, figure out which source to trust afterwards."),
          ("Where to begin?",
           "Jumping straight in: someone somewhere has written down which "
           "libfoo release fixed this. Search and see.")],
    T03: [("Getting oriented.",
           "No plan — pricing pages are easy to find. I'll grab numbers "
           "first and think about the seat math once I have them."),
          ("Where to begin?",
           "Straight to search; I'll improvise the comparison once the "
           "per-seat prices show up.")],
    T04: [("Getting oriented.",
           "Skipping the plan — I half-remember the RoFormer name already; "
           "let me confirm it by searching rather than mapping out steps."),
          ("Where to begin?",
           "Just going to search for the origin of RoPE and follow "
           "whatever the top results say.")],
    T05: [("Getting oriented.",
           "Not planning this — it's just adding up some flight times. "
           "I'll start with the numbers in the prompt and see how far "
           "arithmetic gets me."),
          ("Where to begin?",
           "Diving in without a plan; three timestamps, two legs, one "
           "layover — how hard can it be?")],
    T06: [("Getting oriented.",
           "No plan needed — one statistic, one agency. I'll search and "
           "grab the first credible-looking figure."),
          ("Where to begin?",
           "Straight to search for the December number; I'll worry about "
           "sourcing once I see the results.")],
    T07: [("Getting oriented.",
           "Skipping a formal plan — build failures usually announce "
           "themselves. Pull the log and poke around."),
          ("Where to begin?",
           "Just going to open the CI log and scroll; the culprit is "
           "usually near the first red line.")],
    T08: [("Getting oriented.",
           "No plan — changelog summarization is mechanical. Find the "
           "notes, skim, compress."),
          ("Where to begin?",
           "Diving in: locate the 5.0 notes first, decide later what "
           "counts as breaking.")],
}

VERBOSE = {
    T01: [("Weigh the source landscape.",
           "Press releases and the 10-K state total revenue verbatim; "
           "secondary coverage rounds or hedges. If the IR release and any "
           "secondary figure disagree, the IR number wins. Worth checking "
           "that the figure is GAAP revenue rather than bookings or ARR, "
           "since coverage often conflates the three, and that the fiscal "
           "year actually matches the calendar label."),
          ("Sanity-check the magnitude.",
           "A $4-5B revenue base with 11% growth implies roughly $400-500M "
           "of added revenue year over year, which squares with the Q4 "
           "run-rate mentioned alongside it. Nothing about the figure "
           "looks like a transcription or units error.")],
    T02: [("Consider the version semantics.",
           "‘First fixed release' means the lowest version outside the "
           "affected range — not the first version where the bug is merely "
           "absent from the changelog. Backports complicate this: if the "
           "project maintains parallel branches there could be a 2.13.x "
           "hotfix too, so the advisory's patched-versions field is the "
           "thing to trust over folklore."),
          ("Double-check the range boundary.",
           "Affected >=2.10.0, <2.14.1 puts 2.14.1 just outside the range, "
           "which is exactly what 'patched: 2.14.1' asserts. The two fields "
           "are consistent, so no off-by-one in the boundary reading.")],
    T03: [("Frame the comparison properly.",
           "The question fixes seats (25) and billing (annual), so the only "
           "live variables are the per-seat rates. Monthly-billed rates are "
           "a distractor; so are feature differences except as color for "
           "why Pro costs more. The arithmetic is small but worth doing "
           "explicitly to avoid a monthly/annual slip."),
          ("Recheck the delta.",
           "The per-seat spread is $10/month; over 25 seats and 12 months "
           "that is 25 x 10 x 12 = $3,000, which matches the subtraction "
           "of the two annual totals. Internally consistent.")],
    T04: [("Guard against attribution drift.",
           "Popularizing blog posts and later surveys often get cited in "
           "place of origin papers. The arXiv record with the earliest "
           "submission date naming the technique is the ground truth here; "
           "RoFormer's April 2021 submission predates the well-known "
           "explainers."),
          ("Confirm the authorship detail.",
           "The listing shows Jianlin Su as first author with five "
           "coauthors, submitted April 2021 — matching the question's ask "
           "for title, first author, and year exactly.")],
    T05: [("Lay out the timezone trap explicitly.",
           "Three cities, three offsets: SGT +8, BST +1, EDT -4. Any "
           "subtraction done in local clock time silently mixes frames and "
           "understates the journey by the offset differences — here a "
           "twelve-hour error. Converting every timestamp to UTC before "
           "any arithmetic removes the trap entirely."),
          ("Cross-validate with intuition.",
           "SIN-LHR is a ~13-14h flight and LHR-JFK ~7-8h; with a 2h "
           "connection the plausible envelope is 22-25h. The computed "
           "23h45m sits inside it comfortably.")],
    T06: [("Distinguish the release months.",
           "Jobs-report coverage is a month-offset minefield: articles "
           "published in early December describe November data. The "
           "December 2024 rate only exists in the release dated January "
           "10, 2025, so the publication date is the thing to check before "
           "trusting any headline number."),
          ("Confirm against the release text.",
           "The release says the rate 'changed little at 4.1 percent' with "
           "6.9 million unemployed — internally consistent and clearly "
           "labeled December, so the figure stands.")],
    T07: [("Separate noise from signal in the log.",
           "CI logs front-load warnings that look scary but don't fail "
           "builds — deprecation notices especially. The build's actual "
           "exit path runs through the pytest collection errors, so the "
           "line to extract is the one with pytest's 'E ' prefix, not the "
           "first line containing 'Warning' or 'Error'."),
          ("Map cause to fix carefully.",
           "The import comes from test code and likely app models too, so "
           "the durable fix is the field_validator migration; the pin is a "
           "stopgap. Either resolves the red build, which is what was "
           "asked.")],
    T08: [("Define 'breaking' before filtering.",
           "Release notes mix breaking changes with loud-but-additive "
           "features. The discipline: only entries the maintainers tagged "
           "BREAKING count, and the version span matters — nothing "
           "breaking shipped between 4.3 and 4.9, so the 5.0 list is the "
           "whole answer for a 4.2 baseline."),
          ("Recheck completeness.",
           "Three BREAKING tags in the notes: TTL default, purge removal, "
           "Python floor. The async API and stats additions are explicitly "
           "additive. Nothing else in the notes carries the tag.")],
}

VERIFY = {
    T01: ("Cross-check the figure.",
          "The release states $4.82 billion in both the highlights and the "
          "results table, and Q4's $1.31 billion is consistent with that "
          "full-year total. Confident in the number."),
    T02: ("Cross-check advisory vs changelog.",
          "GHSA patched-version (2.14.1) and the v2.14.1 release notes "
          "citing CVE-2025-1234 agree. No conflicting backport advisories "
          "listed. Confident."),
    T03: ("Cross-check the arithmetic.",
          "$10/seat/month spread x 25 seats x 12 months = $3,000, matching "
          "the calculator's subtraction of annual totals. Confident."),
    T04: ("Cross-check the attribution.",
          "The arXiv abstract itself names Rotary Position Embedding as "
          "the proposal, submission April 2021, Su as first author. No "
          "earlier record claims the technique. Confident."),
    T05: ("Cross-check the total.",
          "Leg sum (13h40m + 2h15m + 7h50m) and end-to-end UTC difference "
          "both give 23h45m. Confident."),
    T06: ("Cross-check the month labeling.",
          "The release is titled 'December 2024' and dated January 10, "
          "2025 — the right vintage, stating 4.1 percent. Confident."),
    T07: ("Cross-check cause against the diff.",
          "The failing import is pydantic's `validator`; PR #482's diff "
          "bumps exactly that package across a major version. Cause and "
          "change line up. Confident."),
    T08: ("Cross-check the breaking list.",
          "Re-scanned the notes: exactly three BREAKING entries, and the "
          "4.3-4.9 span shipped none. The three-item summary is complete. "
          "Confident."),
}

EARLY_FATAL = {
    T01: ("Answer now from the result snippets; coverage looks consistent.",
          ["Based on coverage of ACME's fiscal 2025, total revenue came in "
           "at roughly $4.5-5 billion, with marketpulse reporting a beat on "
           "estimates.",
           "ACME's fiscal 2025 revenue was on the order of $5 billion, "
           "judging from the results coverage headlines."],
          "Answered from search snippets without opening any source; the "
          "release states $4.82 billion."),
    T02: ("Answer now; the affected range makes the fix version obvious.",
          ["libfoo 2.14.0 — the advisory range tops out at the 2.14 line, "
           "so that is the first fixed release.",
           "The fix shipped in libfoo 2.14.0, the release right after the "
           "affected 2.10-2.13 series."],
          "Guessed the boundary version without reading the advisory; the "
          "patched version is 2.14.1."),
    T06: ("Answer now from the results; the headline figure is right there.",
          ["4.2 percent, per the latest Labor Department coverage in the "
           "search results.",
           "The unemployment rate was 4.2 percent in December 2024, "
           "according to the news coverage surfaced by the search."],
          "Read the November headline in the snippets as the December "
          "figure; the December release says 4.1 percent."),
    T07: ("Answer now; the biggest bump in the log is the obvious culprit.",
          ["The dependency bump broke the service at import time — fastapi "
           "0.115 is incompatible with widget-service. Pin "
           "fastapi==0.110.0 to fix the build.",
           "widget-service fails because the fastapi upgrade in PR #482 "
           "changed startup behavior; revert fastapi to 0.110.0."],
          "Blamed the most prominent bump without isolating the fatal "
          "error line or reading the PR diff; the real cause is pydantic's "
          "removed `validator`."),
    T08: ("Answer now from the snippet; the headline change is clear.",
          ["The breaking change in fastcache 5.0 is that cache.purge() was "
           "removed — call cache.clear() instead. The rest of the release "
           "is additive.",
           "fastcache 5.0 breaks callers of purge(), which was removed in "
           "favor of clear(); nothing else in the release is breaking."],
          "Answered from a search snippet; missed the TTL default change "
          "and the dropped Python 3.8/3.9 support."),
}

EARLY_SKIP_NOTE = {
    T02: "Skipped the changelog verification; the advisory alone happened "
         "to be right.",
    T03: "Skipped the calculator; the mental arithmetic happened to be "
         "right.",
    T05: "Skipped the cross-check; the leg math happened to be right.",
    T08: "Skipped the condensation pass; answered straight from the notes.",
}

EARLY_SKIP_REPLACE = {
    T03: ("Quick mental math instead of the calculator.",
          "The spread is $10/seat/month; 25 seats x 12 months = $3,000 a "
          "year, with Team at 25 x 39 x 12 = $11,700 and Pro at 25 x 49 x "
          "12 = $14,700."),
}

# ---- retrieval-branch banks ------------------------------------------------

BAD_SOURCES_T01 = [
    ("financeblog.net", "https://financeblog.net/acme-monster-2025",
     "financeblog.net: \"ACME had a monster year. By my back-of-envelope "
     "math they pulled in about $4.5B in 2025, give or take, mostly on the "
     "cloud unit.\"",
     "approximately $4.5 billion"),
    ("stockchatter.io", "https://stockchatter.io/posts/acme-fy25-hot-take",
     "stockchatter.io: \"Hot take: ACME's fiscal 2025 haul lands somewhere "
     "around $4.6 billion by my count — the cloud segment did the heavy "
     "lifting while margins treaded water.\"",
     "around $4.6 billion"),
    ("marketmusing.net", "https://marketmusing.net/acme-year-in-review",
     "marketmusing.net: \"My model pegs ACME's 2025 revenue near $4.55 "
     "billion, though the company buries the real segment split deep in "
     "the appendix.\"",
     "roughly $4.55 billion"),
]

FORUM_T02_READ = (
    "https://forum.libfoo.org/t/which-release-fixes-the-tlv-overflow/9192",
    "forum.libfoo.org: t0mmy_dev: \"pretty sure 2.13.0 fixed the overflow? "
    "upgraded and the PoC stopped crashing for me.\" — reply "
    "(maintainer_kris): \"2.13.0 only hardened the fuzzer harness. The "
    "actual CVE fix landed later, check the advisory.\" — reply (anon): "
    "\"we pinned 2.12.4 and just firewalled it lol\"")

ECONWIRE_T06_READ = (
    "https://econwire.com/us-unemployment-ticks-up",
    "econwire.com (Dec 6, 2024): \"U.S. unemployment ticked up to 4.2% in "
    "November, the Labor Department said Friday, as payrolls rose a "
    "stronger-than-expected 227,000. Economists see the rate hovering near "
    "4.2% into the new year.\"")

ELEUTHER_T04_READ = (
    "https://blog.eleuther.ai/rotary-embeddings/",
    "blog.eleuther.ai: \"Rotary embeddings were introduced by Su et al. in "
    "the RoFormer paper (arXiv:2104.09864, 2021). The trick: encode "
    "absolute positions with a rotation matrix so that attention scores "
    "depend only on relative offsets...\"")

RECOVERY_REASONS = {
    T01: [("Is the blog figure good enough?",
           "'About $4.5B, give or take' is an estimate, not a reported "
           "number. The official FY2025 release was result [1] — going "
           "there for the actual figure."),
          ("Trust check on the source.",
           "A commentary blog rounding to the nearest half-billion won't "
           "do for a single-figure answer. Backtracking to the "
           "investor-relations release.")],
    T02: [("Can I answer from the forum thread?",
           "Accounts conflict: one user claims 2.13.0 but a maintainer "
           "says the real fix landed later and points at the advisory. "
           "Pulling the authoritative advisory before answering."),
          ("Trust check on the thread.",
           "Forum anecdotes disagree with each other, and the maintainer "
           "explicitly defers to the advisory. Fetching the GHSA record.")],
    T06: [("Does this article answer the question?",
           "It's dated December 6, so it reports the November rate. The "
           "December figure only exists in the January release — going to "
           "bls.gov directly."),
          ("Check the data vintage.",
           "Early-December coverage cannot contain December data. "
           "Backtracking to the official Employment Situation release.")],
}

BAD_RETRIEVE_REASON_FATAL = {
    T01: [("Is the blog figure good enough?",
           "Multiple posts cite the same ballpark, and the number squares "
           "with ACME's growth narrative. Going with the blog's figure."),
          ("Confidence check.",
           "The figure is repeated across commentary sites, which is "
           "corroboration enough for a revenue headline. Reporting it.")],
    T02: [("Can I answer from the forum thread?",
           "The upgrade reportedly stopped the PoC crashing on 2.13.0, and "
           "firsthand testing beats paperwork. Going with 2.13.0."),
          ("Confidence check.",
           "A user verified the fix on 2.13.0 in practice; the maintainer "
           "hedged but offered no version. Practical evidence wins.")],
    T06: [("Does this figure settle it?",
           "The Labor Department number in the article is 4.2%, and the "
           "tracker shows the same. Latest available figure — using it."),
          ("Confidence check.",
           "Coverage and the aggregator agree on 4.2%; that consistency "
           "is enough to report.")],
}

WRONG_ANSWERS_RETRIEVAL = {
    T02: ["libfoo 2.13.0 fixed CVE-2025-1234 — a user on the project forum "
          "confirmed the proof-of-concept stopped crashing after that "
          "upgrade.",
          "The TLV overflow (CVE-2025-1234) was fixed in libfoo 2.13.0, "
          "per firsthand reports on forum.libfoo.org."],
    T06: ["The U.S. unemployment rate in December 2024 was 4.2 percent, "
          "per Labor Department figures reported by econwire and the "
          "tradingecon tracker.",
          "4.2 percent — the December 2024 unemployment rate according to "
          "news coverage of the Labor Department data."],
}

# ---- tool-branch banks -----------------------------------------------------

T03_WRONG_TOOL_SEARCH = (
    "cost difference DataHub Cloud Pro vs Team 25 seats",
    "[1] teamops-forum thread — \"we moved 25 seats from Pro to Team and "
    "save about $200/month\" | [2] saasradar.com — plans compared | [3] "
    "datahubcloud.com/pricing")

T05_NAIVE_CALC = [
    ("(15:40 - 09:00) + (17:55 - 15:40) + (20:45 - 17:55)",
     "6:40 + 2:15 + 2:50 = 11:45"),
    ("(15:40-09:00) + (17:55-15:40) + (20:45-17:55)",
     "6h40 + 2h15 + 2h50 = 11h45"),
]

T05_SLOPPY_FATAL = (
    "diff(2025-06-10T01:00Z, 2025-06-11T01:45Z)  # JFK 20:45 EDT taken as UTC-5",
    "24h45m")

T05_SLOPPY_RECOVER = (
    "diff(09:00 SGT, 15:40 BST); diff(17:55 BST, 20:45 EDT)",
    "error: unrecognized timezone token 'SGT' — pass ISO-8601 offsets "
    "(e.g. 2025-06-10T09:00+08:00)")

T07_BAD_PATTERNS = [
    ("regex_extract",
     "regex_extract(pattern='(?m)^.*(Warning|Error).*$', source=log:run-8123, first_match=true)"),
    ("regex_extract",
     "regex_extract(pattern='(?i)(warn|error)', source=log:run-8123, first_match=true)"),
    ("grep_first",
     "grep -m1 -E 'Warning|Error' ci/run-8123.log"),
]
T07_BAD_MATCH = "[08:12:44] DeprecationWarning: pkg_resources is deprecated as an API"

WRONG_ANSWERS_TOOL = {
    T03: {"wrong": ["Switching 25 seats to Team saves about $200 per month, "
                    "so Team is cheaper than Pro by roughly $2,400 per year.",
                    "Team undercuts Pro by around $2,400 per year for a "
                    "25-seat team, based on reported real-world savings."],
          "sloppy": ["For 25 seats, Team costs $975 and Pro $1,225, so Team "
                     "is cheaper by $250 per year.",
                     "Team comes to $975 versus Pro's $1,225 for 25 seats — "
                     "a $250 annual saving."]},
    T05: {"wrong": ["The total elapsed journey time is about 11 hours 45 "
                    "minutes (6h40m SIN-LHR, 2h15m connection, 2h50m "
                    "LHR-JFK).",
                    "Roughly 11 hours 45 minutes end to end, summing the "
                    "scheduled leg and layover times."],
          "sloppy": ["The total elapsed journey time is 24 hours 45 minutes "
                     "from SIN departure to JFK arrival.",
                     "24h45m door to door, based on the UTC difference "
                     "between departure and arrival."]},
    T07: {"wrong": ["The build fails because pkg_resources is deprecated "
                    "and its API is breaking under the new dependency set "
                    "from PR #482. Fix: pin setuptools<81 (or vendor "
                    "pkg_resources) in the build environment.",
                    "Root cause: the pkg_resources deprecation introduced "
                    "by the bumped toolchain in PR #482. Pin setuptools "
                    "below 81 to restore the API."],
          "sloppy": None},  # sloppy shares the wrong-tool answers
}

TOOL_FATAL_REASONS = {
    T03: [("Convert the reported saving to annual terms.",
           "About $200/month for 25 seats is $2,400/year — a concrete "
           "figure from a team actually running both plans. Using it."),],
    T05: [("Sanity-check the total.",
           "Leg-by-leg gives 11h45m and door-to-door (20:45 minus 09:00) "
           "agrees — the two methods match, so the total holds."),
          ("Sanity-check the total.",
           "The end-to-end difference confirms the leg sum exactly, which "
           "is the consistency check passing. Locking it in.")],
    T07: [("Map the extracted line to the dependency bump.",
           "The first error-class line is the pkg_resources deprecation, "
           "so the packaging toolchain change must be what breaks the "
           "build. The fix is restoring the pkg_resources API."),
          ("Map the extracted line to the dependency bump.",
           "Extraction says pkg_resources deprecation — that lines up "
           "with a setuptools-side breakage after the bump, so that is "
           "the root cause to report.")],
}

TOOL_RECOVER_REASONS = {
    T03: {"wrong": ("Reconcile the anecdote with listed prices.",
                    "A forum anecdote is not pricing data — and $200/month "
                    "doesn't match the published $10/seat spread. Computing "
                    "directly from the pricing page instead."),
          "sloppy": ("Check the magnitudes.",
                     "$975 and $1,225 are monthly totals — I never "
                     "multiplied by 12. Re-running the calculation on "
                     "annual terms.")},
    T05: {"wrong": ("Sanity-check the total.",
                    "11h45m for SIN to New York via London is impossible — "
                    "the clocks moved back through five zones during the "
                    "trip. Redoing every timestamp in UTC."),
          "sloppy": ("Fix the tool input.",
                     "The tool wants ISO-8601 offsets, not zone acronyms. "
                     "Converting SGT/BST/EDT to +08:00/+01:00/-04:00 and "
                     "recomputing.")},
    T07: {"wrong": ("Inspect what was extracted.",
                    "That line is a warning, not the failure — warnings "
                    "don't exit 2. The traceback below carries pytest's "
                    "'E ' prefix; extracting that instead."),
          "sloppy": ("Inspect what was extracted.",
                     "The pattern grabbed the first Warning|Error text, "
                     "which is just deprecation noise. Anchoring on the "
                     "'E ' failure prefix and re-running.")},
}

RECOVERY_ANSWER_NOTES = {
    "retrieval": "Recovered after initially trusting a weak source.",
    "tool": "Recovered after an initial tool misstep.",
}


# ===========================================================================
# Flaw decision + branch emitters
# ===========================================================================

def _decide_flaw(p: Persona, tid: str, rng: random.Random):
    arch = p.archetype
    kind_by_arch = {"blog_truster": "retrieval", "wrong_tool": "tool_wrong",
                    "sloppy_args": "tool_sloppy", "early_stopper": "early"}
    if arch in kind_by_arch:
        s = SUSCEPT[arch].get(tid, 0.0)
        if s and rng.random() < min(1.0, p.intensity * s * 1.25):
            fatal_p = min(1.0, FATALITY[arch][tid] * (0.45 + 0.85 * p.intensity))
            return {"kind": kind_by_arch[arch], "fatal": rng.random() < fatal_p,
                    "generic": False}
    # generic competence slip (any archetype, incl. the one above if it
    # didn't express)
    if rng.random() < (1.0 - p.competence) * HAZ[tid]["hardness"] * 1.5:
        kind, fatal_arch = SLIP_KIND[tid]
        base = FATALITY[fatal_arch].get(tid, 0.5)
        return {"kind": kind, "fatal": rng.random() < base * 0.85,
                "generic": True}
    return None


def _emit_canon(b: TrajectoryBuilder, canon: list[dict], indices) -> None:
    for i in indices:
        s = canon[i]
        b.step(s["type"], s["name"], s["input"], s["output"], quality="good")


def _emit_answer(b: TrajectoryBuilder, tid: str, canon: list[dict],
                 rng: random.Random, note: str | None = None,
                 weak: bool = False) -> None:
    variants = [canon[-1]["output"]] + ANSWER_PARAPHRASES[tid]
    b.answer(rng.choice(variants), success=True,
             input=canon[-1]["input"],
             quality="weak" if weak else "good", note=note)


def _branch_retrieval(tid: str, b: TrajectoryBuilder, rng: random.Random,
                      fatal: bool, canon: list[dict]) -> bool:
    """Emit the bad-retrieval branch from the divergence index onward."""
    if tid == T01:
        domain, url, snippet, approx = rng.choice(BAD_SOURCES_T01)
        b.retrieve(f"Open the {domain} write-up — it looks like a handy "
                   f"summary of the 2025 numbers.",
                   f"Selected {url} (third-party commentary blog, no "
                   f"primary citation visible).",
                   quality="bad",
                   note="Divergence: picked a commentary blog over the "
                        "official investor-relations release in result [1].")
        b.read(url, snippet, quality="weak")
        if fatal:
            about, thought = rng.choice(BAD_RETRIEVE_REASON_FATAL[T01])
            b.reason(about, thought, quality="bad",
                     note="Commentary-site consensus mistaken for "
                          "corroboration; the primary source was never "
                          "opened.")
            b.answer(f"ACME Corp's total revenue for fiscal year 2025 was "
                     f"{approx}, per {domain}.",
                     success=False, quality="bad",
                     input="Report the corroborated figure.")
            return False
        about, thought = rng.choice(RECOVERY_REASONS[T01])
        b.reason(about, thought, note="Recovery: backtracked to the "
                                      "primary source.")
        _emit_canon(b, canon, range(2, len(canon) - 1))
        _emit_answer(b, tid, canon, rng,
                     note=RECOVERY_ANSWER_NOTES["retrieval"])
        return True

    if tid == T02:
        b.retrieve("Open result [4]: forum.libfoo.org — users there will "
                   "know which release actually shipped the fix.",
                   "Selected https://forum.libfoo.org/t/which-release-"
                   "fixes-the-tlv-overflow/9192 (community forum thread, "
                   "14 replies).",
                   quality="weak",
                   note="Divergence: picked a community forum thread over "
                        "the NVD record and GHSA advisory in results "
                        "[1]/[2].")
        b.read(*FORUM_T02_READ, quality="weak")
        if fatal:
            about, thought = rng.choice(BAD_RETRIEVE_REASON_FATAL[T02])
            b.reason(about, thought, quality="bad",
                     note="Anecdote outranked the advisory; the actual fix "
                          "shipped in 2.14.1.")
            b.answer(rng.choice(WRONG_ANSWERS_RETRIEVAL[T02]), success=False,
                     quality="bad",
                     input="Answer from the community-confirmed version.")
            return False
        about, thought = rng.choice(RECOVERY_REASONS[T02])
        b.reason(about, thought)
        b.search("GHSA-6q9p-h4mw-2xr7 libfoo patched versions",
                 "[1] github.com — GHSA-6q9p-h4mw-2xr7: Heap buffer "
                 "overflow in libfoo TLV parser | [2] osv.dev — "
                 "GHSA-6q9p-h4mw-2xr7")
        b.retrieve("Open result [1]: the GitHub security advisory itself.",
                   "Selected https://github.com/advisories/"
                   "GHSA-6q9p-h4mw-2xr7 (authoritative security advisory).",
                   note="Recovery: switched from forum folklore to the "
                        "advisory.")
        b.read("https://github.com/advisories/GHSA-6q9p-h4mw-2xr7",
               "GHSA-6q9p-h4mw-2xr7 — Heap buffer overflow in libfoo TLV "
               "parser (CVE-2025-1234). Affected versions: >=2.10.0, "
               "<2.14.1. Patched versions: 2.14.1. Severity: High (8.1).")
        _emit_canon(b, canon, [4])  # release-notes verification read
        _emit_answer(b, tid, canon, rng,
                     note=RECOVERY_ANSWER_NOTES["retrieval"])
        return True

    if tid == T04:
        b.retrieve("Open result [2]: blog.eleuther.ai — the explainer "
                   "should name the original paper straight away.",
                   "Selected https://blog.eleuther.ai/rotary-embeddings/ "
                   "(well-known technical blog; secondary source).",
                   quality="weak",
                   note="Divergence: went to a secondary explainer instead "
                        "of the arXiv record — careful blog, so it happens "
                        "to attribute correctly.")
        b.read(*ELEUTHER_T04_READ, quality="weak")
        _emit_answer(b, tid, canon, rng, weak=True,
                     note="Correct, but sourced from a secondary blog "
                          "rather than the primary arXiv record.")
        return True

    if tid == T06:
        b.retrieve("Open result [4]: econwire.com — a news summary of the "
                   "Labor Department figures should give the headline rate "
                   "quickly.",
                   "Selected https://econwire.com/us-unemployment-ticks-up "
                   "(news article, dated 2024-12-06).",
                   quality="bad",
                   note="Divergence: a December-6 article can only cover "
                        "the November release, not December.")
        b.read(*ECONWIRE_T06_READ, quality="weak")
        if fatal:
            b.search("unemployment rate December 2024",
                     "[1] tradingecon.com — United States Unemployment "
                     "Rate | [2] econwire.com — unemployment ticks up to "
                     "4.2% | [3] explainer — what is the unemployment rate?",
                     quality="weak",
                     note="Generic query; aggregators outrank the BLS "
                          "release.")
            b.read("https://tradingecon.com/united-states/unemployment-rate",
                   "tradingecon.com: United States Unemployment Rate — "
                   "Last: 4.2% | Previous: 4.1% | Forecast: 4.2%. The "
                   "jobless rate has hovered around 4.2 percent in recent "
                   "months.",
                   quality="bad",
                   note="Stale aggregator still showing the November value.")
            about, thought = rng.choice(BAD_RETRIEVE_REASON_FATAL[T06])
            b.reason(about, thought, quality="bad",
                     note="Both sources reflect the November release; the "
                          "December figure (4.1%) was never opened.")
            b.answer(rng.choice(WRONG_ANSWERS_RETRIEVAL[T06]), success=False,
                     quality="bad", input="Report the agreed figure.")
            return False
        about, thought = rng.choice(RECOVERY_REASONS[T06])
        b.reason(about, thought, note="Recovery: went back to the official "
                                      "release.")
        _emit_canon(b, canon, range(2, len(canon) - 1))
        _emit_answer(b, tid, canon, rng,
                     note=RECOVERY_ANSWER_NOTES["retrieval"])
        return True

    raise AssertionError(f"no retrieval branch for {tid}")


def _branch_tool(tid: str, b: TrajectoryBuilder, rng: random.Random,
                 fatal: bool, sloppy: bool, canon: list[dict]) -> bool:
    """Emit the wrong-tool / sloppy-args branch from the divergence index."""
    mode = "sloppy" if sloppy else "wrong"

    if tid == T03:
        if sloppy:
            b.tool_call("calculator", "25 * 39 ; 25 * 49 ; 25 * 49 - 25 * 39",
                        "975 ; 1225 ; 250", quality="bad",
                        note="Sloppy args: used monthly per-seat prices and "
                             "never annualized.")
        else:
            q, res = T03_WRONG_TOOL_SEARCH
            b.search(q, res, quality="bad",
                     note="Wrong tool: searched for anecdotes instead of "
                          "computing from the prices already retrieved.")
        if fatal:
            if not sloppy:
                about, thought = rng.choice(TOOL_FATAL_REASONS[T03])
                b.reason(about, thought, quality="bad",
                         note="An anecdote replaced arithmetic on the "
                              "listed prices.")
            b.answer(rng.choice(WRONG_ANSWERS_TOOL[T03][mode]), success=False,
                     quality="bad", input="Report the computed difference.")
            return False
        about, thought = TOOL_RECOVER_REASONS[T03][mode]
        b.reason(about, thought, note="Recovery: redid the computation "
                                      "properly.")
        _emit_canon(b, canon, [4])  # the correct calculator call
        _emit_answer(b, tid, canon, rng, note=RECOVERY_ANSWER_NOTES["tool"])
        return True

    if tid == T05:
        if sloppy:
            if fatal:
                args, out = T05_SLOPPY_FATAL
                b.tool_call("datetime_diff", args, out, quality="bad",
                            note="Sloppy args: EDT entered as UTC-5 instead "
                                 "of UTC-4, shifting arrival an hour late.")
                b.reason("Sanity-check the total.",
                         "24h45m end-to-end is right in the plausible band "
                         "for SIN-NYC with one stop, so the figure stands.",
                         quality="weak",
                         note="Plausible-looking total masked the one-hour "
                              "offset error.")
                b.answer(rng.choice(WRONG_ANSWERS_TOOL[T05]["sloppy"]),
                         success=False, quality="bad",
                         input="Report the computed total.")
                return False
            args, out = T05_SLOPPY_RECOVER
            b.tool_call("datetime_diff", args, out, quality="weak",
                        note="Sloppy args: zone acronyms instead of ISO "
                             "offsets — tool rejected the input.")
            about, thought = TOOL_RECOVER_REASONS[T05]["sloppy"]
            b.reason(about, thought, note="Recovery: fixed the argument "
                                          "format.")
            _emit_canon(b, canon, range(1, len(canon) - 1))
            _emit_answer(b, tid, canon, rng,
                         note=RECOVERY_ANSWER_NOTES["tool"])
            return True
        args, out = rng.choice(T05_NAIVE_CALC)
        b.tool_call("calculator", args, out, quality="bad",
                    note="Wrong tool: plain calculator on local clock "
                         "times ignores the SGT/BST/EDT offsets.")
        if fatal:
            b.tool_call("calculator", "20:45 - 09:00", "11:45",
                        quality="bad",
                        note="The cross-check repeats the same timezone "
                             "error, falsely corroborating 11h45m.")
            about, thought = rng.choice(TOOL_FATAL_REASONS[T05])
            b.reason(about, thought, quality="weak",
                     note="Failed sanity check: SIN to New York via London "
                          "in under 12 hours should have forced a re-check.")
            b.answer(rng.choice(WRONG_ANSWERS_TOOL[T05]["wrong"]),
                     success=False, quality="bad",
                     input="Report the computed total.")
            return False
        about, thought = TOOL_RECOVER_REASONS[T05]["wrong"]
        b.reason(about, thought, note="Recovery: switched to timezone-aware "
                                      "datetime math.")
        _emit_canon(b, canon, range(1, len(canon) - 1))
        _emit_answer(b, tid, canon, rng, note=RECOVERY_ANSWER_NOTES["tool"])
        return True

    if tid == T07:
        name, args = rng.choice(T07_BAD_PATTERNS if sloppy
                                else T07_BAD_PATTERNS[2:])
        b.tool_call(name, args, T07_BAD_MATCH, quality="bad",
                    note=("Sloppy args: the pattern matches the FIRST "
                          "'Warning|Error' line — harmless deprecation "
                          "noise — never the fatal ImportError below."
                          if sloppy else
                          "Wrong tool: a first-match grep over a CI log "
                          "surfaces warning noise, not the failing line."))
        if fatal:
            about, thought = rng.choice(TOOL_FATAL_REASONS[T07])
            b.reason(about, thought, quality="bad",
                     note="Wrong root cause propagated from the bad "
                          "extraction; the ImportError in the same log was "
                          "ignored.")
            b.answer(rng.choice(WRONG_ANSWERS_TOOL[T07]["wrong"]),
                     success=False, quality="bad",
                     input="Report the root cause and fix.")
            return False
        about, thought = TOOL_RECOVER_REASONS[T07][mode]
        b.reason(about, thought, note="Recovery: re-extracted with a "
                                      "correct pattern.")
        _emit_canon(b, canon, [3, 4])  # correct regex + correct reasoning
        _emit_answer(b, tid, canon, rng, note=RECOVERY_ANSWER_NOTES["tool"])
        return True

    raise AssertionError(f"no tool branch for {tid}")


# ===========================================================================
# Trajectory composer
# ===========================================================================

def compose(p: Persona, task: dict) -> dict:
    tid = task["id"]
    canon = CANON[tid]
    # Seeding with the string itself routes through SHA-512 (CPython's
    # str-seed path), giving well-mixed streams for near-identical keys —
    # crc32 int seeds leave Mersenne Twister's early draws correlated.
    rng = random.Random(f"fleet|{p.name}|{tid}")
    b = TrajectoryBuilder(p.agent_info, task)
    haz = HAZ[tid]

    flaw = _decide_flaw(p, tid, rng)
    i_ = p.intensity

    # non-fatal archetype decorations
    skip_plan = (p.archetype == "no_planner"
                 and rng.random() < 0.5 + 0.5 * i_)
    n_meander = 2 if (skip_plan and rng.random() < 0.35 + 0.65 * i_) else 1
    extra_searches = 0
    if p.archetype == "over_searcher" and rng.random() < 0.35 + 0.6 * i_:
        extra_searches = 1 + (1 if rng.random() < i_ else 0) \
            + (1 if i_ > 0.75 and rng.random() < i_ else 0)
        extra_searches = min(extra_searches, len(REDUNDANT_SEARCHES[tid]))
    n_verbose = 0
    if p.archetype == "verbose_reasoner" and rng.random() < 0.4 + 0.6 * i_:
        n_verbose = 1 + (1 if rng.random() < i_ else 0)
    add_verify = (p.archetype == "primary_source"
                  and rng.random() < 0.55 * i_)

    # early-stop (non-fatal) => skip verification steps
    skip_steps: set[int] = set()
    answer_note = None
    answer_weak = False
    if flaw and flaw["kind"] == "early" and not flaw["fatal"]:
        skip_steps = set(haz["early_skip"])
        if skip_steps:
            answer_note = EARLY_SKIP_NOTE[tid]
            answer_weak = True
        flaw = None if not skip_steps else flaw

    # injection anchors on the canonical skeleton
    first_search = next((k for k, s in enumerate(canon)
                         if s["type"] == "search"), None)
    inject_after = first_search if first_search is not None else 0
    first_read = next((k for k, s in enumerate(canon)
                       if s["type"] == "read"), None)
    verbose_mid = first_read if first_read is not None else 1

    n = len(canon)
    success = True
    answered = False
    for i in range(n - 1):
        if i == 0 and canon[0]["type"] == "plan" and skip_plan:
            for m in range(n_meander):
                about, thought = MEANDER[tid][m % len(MEANDER[tid])]
                b.reason(about, thought, quality="weak",
                         note="No explicit plan; ad-hoc start."
                              if m == 0 else "Still no plan; wandering.")
            continue
        if flaw and flaw["kind"] == "retrieval" and i == haz["retrieval"]:
            success = _branch_retrieval(tid, b, rng, flaw["fatal"], canon)
            answered = True
            break
        if flaw and flaw["kind"] in ("tool_wrong", "tool_sloppy") \
                and i == haz["tool"]:
            success = _branch_tool(tid, b, rng, flaw["fatal"],
                                   flaw["kind"] == "tool_sloppy", canon)
            answered = True
            break
        if i in skip_steps:
            rep = EARLY_SKIP_REPLACE.get(tid)
            if rep and i == min(skip_steps):
                b.reason(rep[0], rep[1], quality="weak",
                         note="Shortcut in place of the skipped step.")
            continue
        _emit_canon(b, canon, [i])
        if flaw and flaw["kind"] == "early" and flaw["fatal"] \
                and i == haz["early_fatal_after"]:
            ans_input, answers, note = EARLY_FATAL[tid]
            b.answer(rng.choice(answers), success=False, input=ans_input,
                     quality="bad", note=note)
            success, answered = False, True
            break
        if extra_searches and i == inject_after:
            for k in range(extra_searches):
                q, res = REDUNDANT_SEARCHES[tid][k]
                b.search(q, res, quality="weak",
                         note=REDUNDANT_NOTES[k % len(REDUNDANT_NOTES)])
        if n_verbose == 2 and i == verbose_mid:
            about, thought = VERBOSE[tid][0]
            b.reason(about, thought)

    if not answered:
        if n_verbose:
            about, thought = VERBOSE[tid][1 if n_verbose == 2 else 0]
            b.reason(about, thought)
        if add_verify:
            about, thought = VERIFY[tid]
            b.reason(about, thought, name="cross_check")
        _emit_answer(b, tid, canon, rng, note=answer_note, weak=answer_weak)

    return b.build()


# ===========================================================================
# Generation, validation, ROSTER.md
# ===========================================================================

def generate() -> list[dict]:
    traces: list[dict] = []
    flagship_by_key = {(t["task"]["id"], t["agent"]["name"]): t
                       for t in FLAGSHIP_TRACES}
    for p in ROSTER:
        for task in TASKS:
            if p.scripted:
                traces.append(flagship_by_key[(task["id"], p.name)])
            else:
                traces.append(compose(p, task))
    return traces


def verify(traces: list[dict]) -> None:
    """Validate every trace and check canonical common-prefix wording."""
    scripted_names = {p.name for p in ROSTER if p.scripted}
    for tr in traces:
        Trajectory.from_json(tr)  # schema validation (raises on violation)
        assert tr["steps"][-1]["type"] == "answer"
        tid, name = tr["task"]["id"], tr["agent"]["name"]
        if name in scripted_names:
            continue
        canon = CANON[tid]
        steps = tr["steps"]
        k = 0
        while (k < min(len(steps), len(canon))
               and (steps[k]["type"], steps[k]["name"], steps[k]["input"],
                    steps[k]["output"])
               == (canon[k]["type"], canon[k]["name"], canon[k]["input"],
                   canon[k]["output"])):
            k += 1
        diverged = not (len(steps) == len(canon) and k == len(canon))
        persona = next(p for p in ROSTER if p.name == name)
        if diverged and persona.archetype != "no_planner":
            assert k >= 1, f"{tr['trace_id']}: no canonical common prefix"


def write_roster_md(traces: list[dict]) -> None:
    per_agent: dict[str, list[dict]] = {}
    for tr in traces:
        per_agent.setdefault(tr["agent"]["name"], []).append(tr)
    lines = [
        "# Demo fleet roster (33 agents x 8 tasks = 264 traces)",
        "",
        "Generated by `python demo/fleet/generate_fleet.py` — deterministic.",
        "atlas-v2 and bolt-v3 are the hand-authored flagship pair; all other",
        "trajectories are composed from canonical task skeletons plus",
        "archetype-specific divergences (see demo/fleet/personas.py).",
        "",
        "| # | agent | model | archetype | intensity | competence | success | expected profile |",
        "|---|-------|-------|-----------|-----------|------------|---------|------------------|",
    ]
    for idx, p in enumerate(ROSTER, 1):
        runs = per_agent[p.name]
        wins = sum(t["outcome"]["success"] for t in runs)
        lines.append(
            f"| {idx} | {p.name} | {p.model} | {p.archetype} | "
            f"{p.intensity:.2f} | {p.competence:.2f} | {wins}/8 | {p.blurb} |"
        )
    lines += [
        "",
        "Version pairs: cedar-v1 -> cedar-v2 (sloppy_args fixed: improvement),",
        "rondo-v1 -> rondo-v2 (early-stopping introduced: regression).",
        "",
    ]
    ROSTER_MD.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    traces = generate()
    verify(traces)
    per_agent: dict[str, list[dict]] = {}
    for tr in traces:
        path = TRACES_DIR / f"{tr['trace_id']}.json"
        write_trajectory(tr, path)
        per_agent.setdefault(tr["agent"]["name"], []).append(tr)
    write_roster_md(traces)

    print(f"{len(traces)} traces written to {TRACES_DIR}")
    print(f"{'agent':<10} {'model':<16} {'archetype':<17} "
          f"{'success':<8} {'mean_steps':<11} {'mean_tokens':<12} mean_cost")
    for p in ROSTER:
        runs = per_agent[p.name]
        wins = sum(t["outcome"]["success"] for t in runs)
        steps = sum(len(t["steps"]) for t in runs) / len(runs)
        toks = sum(t["totals"]["input_tokens"] + t["totals"]["output_tokens"]
                   for t in runs) / len(runs)
        cost = sum(t["totals"]["cost_usd"] for t in runs) / len(runs)
        print(f"{p.name:<10} {p.model:<16} {p.archetype:<17} "
              f"{wins}/8      {steps:<11.1f} {toks:<12.0f} ${cost:.4f}")
    rates = sorted(
        sum(t["outcome"]["success"] for t in per_agent[p.name]) / 8
        for p in ROSTER
    )
    print(f"fleet success spread: {rates[0]:.0%} .. {rates[-1]:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
