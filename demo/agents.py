"""Two scripted agent personas producing hand-authored trajectories.

- atlas-v2 (model claude-sonnet-5): efficient, plans first, picks
  authoritative sources. Succeeds on 7/8 tasks; its one failure (t07)
  originates in tool execution (a bad regex extraction over a CI log).
- bolt-v3 (model gpt-5): starts identically on most tasks, then diverges:
  three retrieval divergences (t01 fail, t06 fail, t02 late recovery), one
  tool-selection divergence (t05 fail), one over-searching divergence
  (t03, still succeeds). Succeeds on 5/8 tasks.

Alignment contract: in every divergent task both trajectories share
identical wording up to the first diverging step, and the first diverging
step differs genuinely in name and/or input. Every trajectory ends with an
"answer" step.
"""

from __future__ import annotations

from simulator import TrajectoryBuilder
from tasks import TASKS_BY_ID

AGENT_A = {"name": "atlas-v2", "model": "claude-sonnet-5", "version": "v2"}
AGENT_B = {"name": "bolt-v3", "model": "gpt-5", "version": "v3"}

# Documentation of where each scripted divergence begins (0-based step index,
# same index in both trajectories) and what kind it is. Tasks t04 and t08
# have no meaningful divergence.
DIVERGENCES = {
    "t01_acme_revenue": {"index": 2, "kind": "retrieval", "fails": "bolt-v3"},
    "t02_cve_libfoo": {"index": 2, "kind": "retrieval", "fails": None},  # b recovers
    "t03_saas_pricing": {"index": 2, "kind": "stopping", "fails": None},  # b over-searches
    "t05_flight_duration": {"index": 1, "kind": "tool_selection", "fails": "bolt-v3"},
    "t06_bls_unemployment": {"index": 2, "kind": "retrieval", "fails": "bolt-v3"},
    "t07_build_failure": {"index": 3, "kind": "tool_selection", "fails": "atlas-v2"},
}


class Both:
    """Apply the same builder call to several builders, keeping the wording
    of a shared step prefix literally identical between agents."""

    def __init__(self, *builders: TrajectoryBuilder):
        self._builders = builders

    def __getattr__(self, attr):
        def call(*args, **kwargs):
            for b in self._builders:
                getattr(b, attr)(*args, **kwargs)
            return self

        return call


def _pair(task_id: str):
    task = TASKS_BY_ID[task_id]
    a = TrajectoryBuilder(AGENT_A, task)
    b = TrajectoryBuilder(AGENT_B, task)
    return a, b, Both(a, b)


# ---------------------------------------------------------------------------
# t01 — ACME revenue. B diverges at step 2: picks a blog over the official
# investor-relations release, chases circular corroboration, answers wrong.
# ---------------------------------------------------------------------------
def t01_acme_revenue():
    a, b, ab = _pair("t01_acme_revenue")

    ab.plan(
        "1) Search for ACME Corp's FY2025 annual results. 2) Open the "
        "investor-relations press release or 10-K. 3) Extract total revenue "
        "from the full-year highlights. 4) Report the figure with its source.",
        output="Plan set. Starting with a targeted search of investor sources.",
    )
    ab.search(
        "ACME Corp FY2025 annual results total revenue investor relations",
        '[1] ir.acmecorp.com — "ACME Corp Reports Fourth Quarter and Full '
        'Year Fiscal 2025 Results" | [2] marketpulse.io — "ACME beats '
        'estimates as cloud unit accelerates" | [3] financeblog.net — '
        '"What ACME\'s monster 2025 really means for shareholders" | '
        '[4] wikipedia.org — ACME Corp',
    )

    # -- divergence: index 2 --
    a.retrieve(
        "Open result [1]: ir.acmecorp.com — the official FY2025 results "
        "press release from investor relations.",
        "Selected https://ir.acmecorp.com/news/fy2025-results (primary "
        "source: company investor-relations newsroom, dated 2025-05-22).",
    )
    a.read(
        "https://ir.acmecorp.com/news/fy2025-results",
        "ACME Corp Reports Fourth Quarter and Full Year Fiscal 2025 Results. "
        "Full year fiscal 2025 highlights: Total revenue of $4.82 billion, "
        "up 11% year over year. GAAP operating margin of 18.4%. Fourth "
        "quarter revenue of $1.31 billion, up 9% year over year. Cash and "
        "equivalents of $2.1 billion at year end.",
    )
    a.answer(
        "ACME Corp's total revenue for fiscal year 2025 was $4.82 billion, "
        "up 11% year over year, per the company's FY2025 results press "
        "release on its investor-relations site (ir.acmecorp.com).",
        success=True,
    )

    b.retrieve(
        "Open result [3]: financeblog.net — the write-up looks like a handy "
        "summary of the 2025 numbers.",
        "Selected https://financeblog.net/acme-monster-2025 (third-party "
        "commentary blog, no primary citation visible).",
        quality="bad",
        note="Divergence: picked a commentary blog over the official "
        "investor-relations release that was result [1].",
    )
    b.read(
        "https://financeblog.net/acme-monster-2025",
        "financeblog.net: \"ACME had a monster year. By my back-of-envelope "
        "math they pulled in about $4.5B in 2025, give or take, mostly on "
        "the cloud unit. Margins? Who knows — the filing is a maze.\"",
        quality="weak",
    )
    b.reason(
        "Is the blog's revenue figure reliable enough to report?",
        "The blog says 'about $4.5B, give or take' — approximate. I should "
        "corroborate the number with another source before answering.",
        quality="weak",
    )
    b.search(
        "ACME Corp 2025 revenue \"$4.5 billion\"",
        '[1] moneymirror.com — "ACME\'s 2025 revenue came in around $4.5 '
        'billion, analysts say" | [2] stocknewsdaily.net — "ACME posts '
        '~$4.5B year" | [3] financeblog.net — original post',
        quality="weak",
    )
    b.retrieve(
        "Open result [1]: moneymirror.com — an independent outlet repeating "
        "the figure would confirm it.",
        "Selected https://moneymirror.com/acme-2025-revenue (aggregator; "
        "article body links back to financeblog.net as its source).",
        quality="bad",
        note="Circular corroboration: the aggregator cites the same blog, "
        "so this does not independently confirm $4.5B.",
    )
    b.read(
        "https://moneymirror.com/acme-2025-revenue",
        "moneymirror.com: \"ACME's 2025 revenue came in around $4.5 billion, "
        "analysts say, capping a strong year for the cloud division. (h/t "
        "financeblog.net)\"",
        quality="bad",
    )
    b.reason(
        "Do two sources agreeing settle the figure?",
        "Two sources now report roughly $4.5 billion for 2025. That is "
        "consistent, so I will report it.",
        quality="bad",
        note="Both sources trace to the same blog; the official release "
        "(never opened) states $4.82 billion.",
    )
    b.answer(
        "ACME Corp's total revenue for fiscal year 2025 was approximately "
        "$4.5 billion, as reported by financeblog.net and corroborated by "
        "moneymirror.com.",
        success=False,
    )
    return a.build(), b.build()


# ---------------------------------------------------------------------------
# t02 — CVE fix version. B diverges at step 2 to a community forum thread,
# gets conflicting folklore, then recovers via the GHSA advisory. Succeeds,
# but spends ~4 extra steps.
# ---------------------------------------------------------------------------
def t02_cve_libfoo():
    a, b, ab = _pair("t02_cve_libfoo")

    ab.plan(
        "1) Look up CVE-2025-1234 in NVD or the GitHub security advisory. "
        "2) Note the patched version range for libfoo. 3) Verify against the "
        "libfoo release notes. 4) Answer with the first fixed release.",
        output="Plan set. Querying vulnerability databases first.",
    )
    ab.search(
        "CVE-2025-1234 libfoo fixed version advisory",
        "[1] nvd.nist.gov — CVE-2025-1234 Detail | [2] github.com — "
        "GHSA-6q9p-h4mw-2xr7: Heap buffer overflow in libfoo TLV parser | "
        "[3] news.ycombinator.com — \"libfoo overflow, patch your fleets\" | "
        "[4] forum.libfoo.org — \"Which release fixes the TLV overflow?\"",
    )

    # -- divergence: index 2 --
    a.retrieve(
        "Open result [1]: nvd.nist.gov — the NVD entry is the authoritative "
        "record; the GHSA in result [2] can confirm.",
        "Selected https://nvd.nist.gov/vuln/detail/CVE-2025-1234 (primary "
        "vulnerability database record).",
    )
    a.read(
        "https://nvd.nist.gov/vuln/detail/CVE-2025-1234",
        "NVD — CVE-2025-1234: Heap buffer overflow in libfoo's TLV parser "
        "(parse_tlv.c) allows remote code execution via a crafted length "
        "field. Affected: >=2.10.0, <2.14.1. CVSS 3.1: 8.1 (HIGH). "
        "References: GHSA-6q9p-h4mw-2xr7 — 'Patched versions: 2.14.1'.",
    )
    a.read(
        "https://github.com/libfoo/libfoo/releases/tag/v2.14.1",
        "libfoo 2.14.1 (2025-03-04) release notes: SECURITY: fix heap "
        "buffer overflow in parse_tlv on malformed length fields "
        "(CVE-2025-1234). Backport of #4182. All users on 2.10.x-2.14.0 "
        "should upgrade immediately.",
    )
    a.answer(
        "libfoo 2.14.1 is the first released version that fixes "
        "CVE-2025-1234 (heap buffer overflow in the TLV parser). Confirmed "
        "by the NVD/GHSA advisory (affected <2.14.1, patched 2.14.1) and "
        "the v2.14.1 release notes.",
        success=True,
    )

    b.retrieve(
        "Open result [4]: forum.libfoo.org — users there will know which "
        "release actually shipped the fix.",
        "Selected https://forum.libfoo.org/t/which-release-fixes-the-tlv-"
        "overflow/9192 (community forum thread, 14 replies).",
        quality="weak",
        note="Divergence: picked a community forum thread over the NVD "
        "record and GHSA advisory in results [1] and [2].",
    )
    b.read(
        "https://forum.libfoo.org/t/which-release-fixes-the-tlv-overflow/9192",
        "forum.libfoo.org: t0mmy_dev: \"pretty sure 2.13.0 fixed the "
        "overflow? upgraded and the PoC stopped crashing for me.\" — "
        "reply (maintainer_kris): \"2.13.0 only hardened the fuzzer "
        "harness. The actual CVE fix landed later, check the advisory.\" — "
        "reply (anon): \"we pinned 2.12.4 and just firewalled it lol\"",
        quality="weak",
    )
    b.reason(
        "Can I answer from the forum thread?",
        "Accounts conflict: one user claims 2.13.0, but a maintainer says "
        "the real fix landed later and points at the advisory. I need the "
        "authoritative advisory before answering.",
    )
    b.search(
        "GHSA-6q9p-h4mw-2xr7 libfoo patched versions",
        "[1] github.com — GHSA-6q9p-h4mw-2xr7: Heap buffer overflow in "
        "libfoo TLV parser | [2] osv.dev — GHSA-6q9p-h4mw-2xr7",
    )
    b.retrieve(
        "Open result [1]: the GitHub security advisory itself.",
        "Selected https://github.com/advisories/GHSA-6q9p-h4mw-2xr7 "
        "(authoritative security advisory).",
        note="Recovery: switched from forum folklore to the advisory.",
    )
    b.read(
        "https://github.com/advisories/GHSA-6q9p-h4mw-2xr7",
        "GHSA-6q9p-h4mw-2xr7 — Heap buffer overflow in libfoo TLV parser "
        "(CVE-2025-1234). Affected versions: >=2.10.0, <2.14.1. Patched "
        "versions: 2.14.1. Severity: High (8.1).",
    )
    b.read(
        "https://github.com/libfoo/libfoo/releases/tag/v2.14.1",
        "libfoo 2.14.1 (2025-03-04) release notes: SECURITY: fix heap "
        "buffer overflow in parse_tlv on malformed length fields "
        "(CVE-2025-1234). Backport of #4182. All users on 2.10.x-2.14.0 "
        "should upgrade immediately.",
    )
    b.reason(
        "Reconcile the forum claim with the advisory.",
        "The advisory and 2.14.1 release notes agree: patched in 2.14.1. "
        "The forum's 2.13.0 claim was wrong, as the maintainer implied. "
        "Answer: 2.14.1.",
    )
    b.answer(
        "libfoo 2.14.1 is the first released version that fixes "
        "CVE-2025-1234. The GHSA advisory lists patched version 2.14.1 "
        "(affected >=2.10.0, <2.14.1), and the v2.14.1 release notes cite "
        "the CVE directly. (An earlier forum claim of 2.13.0 was incorrect.)",
        success=True,
    )
    return a.build(), b.build()


# ---------------------------------------------------------------------------
# t03 — SaaS pricing. B diverges at step 2 by issuing redundant searches and
# a redundant confirmation read (stopping/efficiency divergence). Both
# succeed with the same numbers.
# ---------------------------------------------------------------------------
def t03_saas_pricing():
    a, b, ab = _pair("t03_saas_pricing")

    ab.plan(
        "1) Get per-seat prices for DataHub Cloud's Pro and Team plans from "
        "the official pricing page. 2) Compute the 25-seat annual cost of "
        "each plan. 3) Compare and report the difference.",
        output="Plan set. Locating the official pricing page.",
    )
    ab.search(
        "DataHub Cloud pricing Pro Team plan per seat annual billing",
        "[1] datahubcloud.com/pricing — Plans & Pricing | [2] saasradar.com "
        "— \"DataHub Cloud plans compared (2025)\" | [3] reddit.com — "
        "r/dataeng: \"Is DataHub Cloud Pro worth it?\"",
    )

    # -- divergence: index 2 --
    a.retrieve(
        "Open result [1]: datahubcloud.com/pricing — the vendor's own "
        "pricing page has the canonical numbers.",
        "Selected https://datahubcloud.com/pricing (official pricing page).",
    )
    a.read(
        "https://datahubcloud.com/pricing",
        "DataHub Cloud — Plans & Pricing. Team: $39 per user/month billed "
        "annually ($47 billed monthly). Pro: $49 per user/month billed "
        "annually ($59 billed monthly); adds SSO/SAML, audit logs, 90-day "
        "version history, priority support. Enterprise: contact sales.",
    )
    a.tool_call(
        "calculator",
        "25 * 39 * 12 ; 25 * 49 * 12 ; 25 * 49 * 12 - 25 * 39 * 12",
        "11700 ; 14700 ; 3000",
    )
    a.answer(
        "For a 25-seat team billed annually: Team costs $11,700/year "
        "(25 x $39 x 12) and Pro costs $14,700/year (25 x $49 x 12). Team "
        "is cheaper by $3,000 per year; Pro's premium buys SSO, audit logs, "
        "and 90-day history.",
        success=True,
    )

    b.search(
        "DataHub Cloud Team plan price per user 2025",
        "[1] datahubcloud.com/pricing — Plans & Pricing | [2] saasradar.com "
        "— \"DataHub Cloud plans compared (2025)\" | [3] getapp-style "
        "listing — DataHub Cloud pricing overview",
        quality="weak",
        note="Divergence: redundant follow-up query — the official pricing "
        "page was already result [1] of the first search.",
    )
    b.search(
        "DataHub Cloud Pro plan annual billing discount",
        "[1] datahubcloud.com/pricing — Plans & Pricing | [2] "
        "datahubcloud.com/blog — \"Annual billing now saves 17%\" | [3] "
        "saasradar.com — plans compared",
        quality="weak",
        note="Second redundant query; still landing on the same page.",
    )
    b.retrieve(
        "Open result [1]: datahubcloud.com/pricing — the vendor's own "
        "pricing page has the canonical numbers.",
        "Selected https://datahubcloud.com/pricing (official pricing page).",
    )
    b.read(
        "https://datahubcloud.com/pricing",
        "DataHub Cloud — Plans & Pricing. Team: $39 per user/month billed "
        "annually ($47 billed monthly). Pro: $49 per user/month billed "
        "annually ($59 billed monthly); adds SSO/SAML, audit logs, 90-day "
        "version history, priority support. Enterprise: contact sales.",
    )
    b.read(
        "https://saasradar.com/datahub-cloud-plans-compared-2025",
        "saasradar.com: \"DataHub Cloud's Team tier runs $39/user/mo on "
        "annual terms versus $49 for Pro. For most sub-50-seat teams the "
        "delta is SSO and audit logs.\"",
        quality="weak",
        note="Confirmation read adds latency but no new information.",
    )
    b.tool_call(
        "calculator",
        "25 * 39 * 12 ; 25 * 49 * 12 ; 25 * 49 * 12 - 25 * 39 * 12",
        "11700 ; 14700 ; 3000",
    )
    b.answer(
        "Billed annually for 25 seats, the Team plan costs $11,700/year "
        "(25 x $39 x 12) versus $14,700/year for Pro (25 x $49 x 12), so "
        "Team is cheaper by $3,000 per year. Pro's extra cost covers SSO, "
        "audit logs, and 90-day version history.",
        success=True,
    )
    return a.build(), b.build()


# ---------------------------------------------------------------------------
# t04 — RoPE paper. No divergence: both agents run a clean 5-step lookup.
# B's wording differs only trivially in the final two steps.
# ---------------------------------------------------------------------------
def t04_rope_paper():
    a, b, ab = _pair("t04_rope_paper")

    ab.plan(
        "1) Search for the paper that introduced Rotary Position Embedding. "
        "2) Open the arXiv record. 3) Confirm the title, first author, and "
        "year from the abstract page. 4) Answer.",
        output="Plan set. Searching for the original RoPE paper.",
    )
    ab.search(
        "rotary position embedding RoPE original paper arxiv",
        "[1] arxiv.org/abs/2104.09864 — \"RoFormer: Enhanced Transformer "
        "with Rotary Position Embedding\" | [2] blog.eleuther.ai — \"Rotary "
        "Embeddings: A Relative Revolution\" | [3] paperswithcode.com — "
        "Rotary Position Embedding",
    )
    ab.retrieve(
        "Open result [1]: arxiv.org/abs/2104.09864 — the arXiv abstract "
        "page identifies the introducing paper directly.",
        "Selected https://arxiv.org/abs/2104.09864 (arXiv abstract page, "
        "primary record).",
    )
    ab.read(
        "https://arxiv.org/abs/2104.09864",
        "arXiv:2104.09864 — \"RoFormer: Enhanced Transformer with Rotary "
        "Position Embedding\". Jianlin Su, Yu Lu, Shengfeng Pan, Ahmed "
        "Murtadha, Bo Wen, Yunfeng Liu. Submitted 20 April 2021. Abstract: "
        "...we propose Rotary Position Embedding (RoPE) to effectively "
        "leverage positional information, encoding absolute position with a "
        "rotation matrix while incorporating relative position dependency "
        "in self-attention...",
    )

    a.answer(
        "Rotary Position Embedding (RoPE) was introduced in \"RoFormer: "
        "Enhanced Transformer with Rotary Position Embedding\" by Jianlin "
        "Su et al., first posted to arXiv in April 2021 (arXiv:2104.09864).",
        success=True,
    )
    b.answer(
        "RoPE was first introduced in the 2021 paper \"RoFormer: Enhanced "
        "Transformer with Rotary Position Embedding\" (arXiv:2104.09864), "
        "whose first author is Jianlin Su.",
        success=True,
    )
    return a.build(), b.build()


# ---------------------------------------------------------------------------
# t05 — Flight duration. B diverges at step 1: reaches for a plain
# calculator on local clock times instead of timezone-aware datetime math.
# The naive cross-check repeats the same error, so B confidently fails.
# ---------------------------------------------------------------------------
def t05_flight_duration():
    a, b, ab = _pair("t05_flight_duration")

    ab.plan(
        "1) Work out the duration of each flight leg and the layover from "
        "the scheduled times. 2) Sum them. 3) Sanity-check the total against "
        "the end-to-end departure/arrival difference. 4) Answer.",
        output="Plan set. Working through the legs in order.",
    )

    # -- divergence: index 1 --
    a.reason(
        "Normalize every timestamp before any arithmetic.",
        "These times are in three zones, so convert to UTC first: SGT is "
        "UTC+8, BST is UTC+1, EDT is UTC-4. SIN dep 09:00 SGT = 01:00 UTC. "
        "LHR arr 15:40 BST = 14:40 UTC; LHR dep 17:55 BST = 16:55 UTC. JFK "
        "arr 20:45 EDT = 00:45 UTC on 11 June.",
    )
    a.tool_call(
        "datetime_diff",
        "diff(2025-06-10T01:00Z, 2025-06-10T14:40Z); "
        "diff(2025-06-10T14:40Z, 2025-06-10T16:55Z); "
        "diff(2025-06-10T16:55Z, 2025-06-11T00:45Z)",
        "13h40m; 2h15m; 7h50m",
    )
    a.tool_call(
        "datetime_diff",
        "sum(13h40m, 2h15m, 7h50m); diff(2025-06-10T01:00Z, 2025-06-11T00:45Z)",
        "23h45m; 23h45m",
    )
    a.reason(
        "Cross-check the leg sum against the end-to-end difference.",
        "Leg sum (13h40m flight + 2h15m layover + 7h50m flight) is 23h45m, "
        "and the end-to-end UTC difference is also 23h45m. Consistent, and "
        "plausible for SIN-LHR-JFK.",
    )
    a.answer(
        "The total elapsed journey time is 23 hours 45 minutes: 13h40m for "
        "SQ306 SIN-LHR, a 2h15m connection at Heathrow, and 7h50m for "
        "BA117 LHR-JFK (all computed in UTC).",
        success=True,
    )

    b.tool_call(
        "calculator",
        "(15:40 - 09:00) + (17:55 - 15:40) + (20:45 - 17:55)",
        "6:40 + 2:15 + 2:50 = 11:45",
        quality="bad",
        note="Divergence: used a plain calculator on local clock times. "
        "SGT/BST/EDT offsets are ignored, so every leg duration is wrong.",
    )
    b.tool_call(
        "calculator",
        "20:45 - 09:00",
        "11:45",
        quality="bad",
        note="The cross-check repeats the same timezone error, so it "
        "falsely corroborates 11h45m.",
    )
    b.reason(
        "Sanity-check the total.",
        "Leg-by-leg gives 11h45m and door-to-door (20:45 minus 09:00) also "
        "gives 11h45m — the two methods agree, so the total holds.",
        quality="weak",
        note="Failed sanity check: SIN to New York via London in under 12 "
        "hours should have triggered a re-check of timezone handling.",
    )
    b.answer(
        "The total elapsed journey time is about 11 hours 45 minutes "
        "(6h40m SIN-LHR, 2h15m connection, 2h50m LHR-JFK).",
        success=False,
    )
    return a.build(), b.build()


# ---------------------------------------------------------------------------
# t06 — BLS unemployment. B diverges at step 2 to a news article that covers
# the November release, then "confirms" with a stale aggregator. Fails.
# ---------------------------------------------------------------------------
def t06_bls_unemployment():
    a, b, ab = _pair("t06_bls_unemployment")

    ab.plan(
        "1) Find the BLS Employment Situation release covering December "
        "2024. 2) Read the headline unemployment rate from bls.gov. "
        "3) Answer with the official figure.",
        output="Plan set. Searching for the official release.",
    )
    ab.search(
        "BLS Employment Situation December 2024 unemployment rate",
        "[1] bls.gov/news.release/archives/empsit_01102025.htm — The "
        "Employment Situation — December 2024 | [2] bls.gov/cps — Labor "
        "Force Statistics (CPS) | [3] tradingecon.com — United States "
        "Unemployment Rate | [4] econwire.com — \"U.S. unemployment ticks "
        "up to 4.2%, Labor Department says\"",
    )

    # -- divergence: index 2 --
    a.retrieve(
        "Open result [1]: bls.gov — the Employment Situation news release "
        "for December 2024 is the official source.",
        "Selected https://www.bls.gov/news.release/archives/"
        "empsit_01102025.htm (official BLS news release, 2025-01-10).",
    )
    a.read(
        "https://www.bls.gov/news.release/archives/empsit_01102025.htm",
        "THE EMPLOYMENT SITUATION — DECEMBER 2024. Total nonfarm payroll "
        "employment increased by 256,000 in December, and the unemployment "
        "rate changed little at 4.1 percent, the U.S. Bureau of Labor "
        "Statistics reported today. The number of unemployed people, at "
        "6.9 million, changed little over the month.",
    )
    a.answer(
        "Per the BLS Employment Situation release of January 10, 2025, the "
        "U.S. unemployment rate in December 2024 was 4.1 percent.",
        success=True,
    )

    b.retrieve(
        "Open result [4]: econwire.com — a news summary of the Labor "
        "Department figures should give the headline rate quickly.",
        "Selected https://econwire.com/us-unemployment-ticks-up (news "
        "article, dated 2024-12-06).",
        quality="bad",
        note="Divergence: picked a news article dated December 6, which "
        "can only cover the November 2024 release, over the official BLS "
        "December release in result [1].",
    )
    b.read(
        "https://econwire.com/us-unemployment-ticks-up",
        "econwire.com (Dec 6, 2024): \"U.S. unemployment ticked up to 4.2% "
        "in November, the Labor Department said Friday, as payrolls rose a "
        "stronger-than-expected 227,000. Economists see the rate hovering "
        "near 4.2% into the new year.\"",
        quality="weak",
    )
    b.reason(
        "Does this article answer the question?",
        "The article is dated December 6 and reports the November rate "
        "(4.2%). I need the December figure — searching for something more "
        "recent.",
    )
    b.search(
        "unemployment rate December 2024",
        "[1] tradingecon.com — United States Unemployment Rate | [2] "
        "econwire.com — \"U.S. unemployment ticks up to 4.2%\" | [3] "
        "usafacts-style explainer — What is the unemployment rate?",
        quality="weak",
        note="Generic query; did not scope to bls.gov, so aggregators rank "
        "first.",
    )
    b.retrieve(
        "Open result [1]: tradingecon.com — the tracker chart should have "
        "the newest monthly value.",
        "Selected https://tradingecon.com/united-states/unemployment-rate "
        "(aggregator chart; no release citation or as-of date shown).",
        quality="bad",
        note="Aggregator page is stale — it still shows the November value "
        "and does not label which month 'last' refers to.",
    )
    b.read(
        "https://tradingecon.com/united-states/unemployment-rate",
        "tradingecon.com: United States Unemployment Rate — Last: 4.2% | "
        "Previous: 4.1% | Forecast: 4.2%. The jobless rate has hovered "
        "around 4.2 percent in recent months.",
        quality="bad",
    )
    b.reason(
        "Reconcile the sources.",
        "The news article says 4.2% and the tracker's latest value is also "
        "4.2%. Two sources agree, so December 2024 must be 4.2%.",
        quality="bad",
        note="Both sources reflect the November release; the BLS December "
        "figure (4.1%) was never opened.",
    )
    b.answer(
        "The U.S. unemployment rate in December 2024 was 4.2 percent, "
        "according to Labor Department figures reported by econwire and "
        "the tradingecon tracker.",
        success=False,
    )
    return a.build(), b.build()


# ---------------------------------------------------------------------------
# t07 — CI build failure. Shared prefix of 3 steps; the divergence at step 3
# is ATLAS's: its regex extraction grabs the first Warning|Error line — a
# harmless DeprecationWarning — and the wrong root cause propagates to a
# wrong answer. Bolt writes a correct pattern and succeeds.
# ---------------------------------------------------------------------------
def t07_build_failure():
    a, b, ab = _pair("t07_build_failure")

    ab.plan(
        "1) Pull the failing CI log for widget-service. 2) Isolate the "
        "first fatal error. 3) Map it to a dependency changed in PR #482. "
        "4) Propose the fix.",
        output="Plan set. Fetching the most recent failed run.",
    )
    ab.tool_call(
        "ci_get_log",
        "ci.get_log(repo='widget-service', run='last-failed')  # run 8123, job: test",
        "[08:12:01] Installing dependencies from lockfile (pydantic 3.0.0, "
        "fastapi 0.115.2, sqlalchemy 2.0.36, ...)\n"
        "[08:12:44] DeprecationWarning: pkg_resources is deprecated as an API\n"
        "[08:12:45] tests/test_models.py:3: in <module>\n"
        "[08:12:45]     from pydantic import BaseModel, validator\n"
        "[08:12:45] E   ImportError: cannot import name 'validator' from "
        "'pydantic' (/usr/lib/python3.12/site-packages/pydantic/__init__.py)\n"
        "[08:12:46] ERROR: 14 errors during collection\n"
        "[08:12:46] Job failed with exit code 2",
    )
    ab.read(
        "https://github.com/acme/widget-service/pull/482/files",
        "PR #482 \"chore: bump dependencies\" — requirements.txt diff: "
        "-pydantic==2.7.1 +pydantic==3.0.0 | -fastapi==0.110.0 "
        "+fastapi==0.115.2 | -sqlalchemy==2.0.30 +sqlalchemy==2.0.36",
    )

    # -- divergence: index 3 --
    a.tool_call(
        "regex_extract",
        r"regex_extract(pattern='(?m)^.*(Warning|Error).*$', source=log:run-8123, first_match=true)",
        "[08:12:44] DeprecationWarning: pkg_resources is deprecated as an API",
        quality="bad",
        note="Tool misuse: the pattern matches the FIRST 'Warning|Error' "
        "line, which is a harmless DeprecationWarning; the fatal "
        "ImportError two lines below is never extracted.",
    )
    a.reason(
        "Map the extracted line to the dependency bump.",
        "The first error-class line is the pkg_resources deprecation, so "
        "the setuptools/pkg_resources change must be what broke the build "
        "after the bump. The fix is to restore the pkg_resources API.",
        quality="bad",
        note="Wrong root cause propagated from the bad extraction; the "
        "ImportError in the same log was ignored.",
    )
    a.answer(
        "The build fails because pkg_resources is deprecated and its API "
        "is no longer available after the dependency bump in PR #482. Fix: "
        "pin setuptools<81 (or vendor pkg_resources) in widget-service's "
        "build environment.",
        success=False,
    )

    b.tool_call(
        "regex_extract",
        r"regex_extract(pattern='(?m)^E\s+\w+Error.*$', source=log:run-8123)",
        "E   ImportError: cannot import name 'validator' from 'pydantic' "
        "(/usr/lib/python3.12/site-packages/pydantic/__init__.py)",
        note="Anchored on pytest's 'E ' failure prefix, so the fatal line "
        "is extracted rather than the earlier deprecation noise.",
    )
    b.reason(
        "Map the extracted line to the dependency bump.",
        "The fatal line is an ImportError: `validator` no longer exists in "
        "pydantic. PR #482 bumped pydantic 2.7.1 -> 3.0.0, and pydantic 3 "
        "removed the v1-style `validator` export. The DeprecationWarning "
        "about pkg_resources is unrelated noise. Fix: migrate decorators "
        "to `field_validator` or pin pydantic<3.",
    )
    b.answer(
        "Root cause: PR #482 bumped pydantic from 2.7.1 to 3.0.0, which "
        "removed the v1-style `validator` import that "
        "tests/test_models.py (and the app models) still use, so test "
        "collection dies with ImportError. Fix: migrate `@validator` to "
        "`@field_validator` (pydantic v2/v3 style) — or pin "
        "pydantic==2.7.1 until the migration lands.",
        success=True,
    )
    return a.build(), b.build()


# ---------------------------------------------------------------------------
# t08 — Changelog diff. No divergence: both agents do a clean 6-step run;
# only the final condensation/answer wording differs slightly.
# ---------------------------------------------------------------------------
def t08_changelog_diff():
    a, b, ab = _pair("t08_changelog_diff")

    ab.plan(
        "1) Find the fastcache 5.0 release notes or changelog. 2) Collect "
        "every entry marked breaking since 4.2. 3) Condense them into a "
        "short summary.",
        output="Plan set. Looking for the official release notes.",
    )
    ab.search(
        "fastcache 5.0 changelog breaking changes since 4.2",
        "[1] github.com/fastcache/fastcache/releases — Releases | [2] "
        "fastcache.dev/changelog — Changelog | [3] stackoverflow.com — "
        "\"fastcache 5.0 purge() gone?\"",
    )
    ab.retrieve(
        "Open result [1]: the GitHub releases page lists 5.0.0 with its "
        "full notes, which is the primary record.",
        "Selected https://github.com/fastcache/fastcache/releases/tag/"
        "v5.0.0 (official release notes).",
    )
    ab.read(
        "https://github.com/fastcache/fastcache/releases/tag/v5.0.0",
        "fastcache v5.0.0 — Highlights & breaking changes. BREAKING: "
        "Cache() now defaults to ttl=300 seconds; previously entries never "
        "expired unless ttl was set. BREAKING: cache.purge() has been "
        "removed; call cache.clear() instead. BREAKING: dropped support "
        "for Python 3.8 and 3.9; fastcache now requires Python >=3.10. "
        "Added: native asyncio API (AsyncCache), LRU eviction statistics, "
        "typed stubs. Fixed: race in concurrent set() under free-threading. "
        "(No breaking changes were shipped in 4.3-4.9.)",
    )

    a.reason(
        "Filter the notes down to what the question asks.",
        "Only entries labeled BREAKING matter for 4.2 -> 5.0: the TTL "
        "default change, the purge() removal, and the dropped Python "
        "versions. The asyncio API and eviction stats are additive.",
    )
    a.answer(
        "Between fastcache 4.2 and 5.0 there are three breaking changes: "
        "(1) Cache() now defaults to a 300-second TTL where entries "
        "previously never expired; (2) cache.purge() was removed — use "
        "cache.clear(); (3) Python 3.8/3.9 support was dropped, so 5.0 "
        "requires Python >=3.10. New features (async API, LRU stats) are "
        "additive, not breaking.",
        success=True,
    )

    b.reason(
        "Filter the notes down to what the question asks.",
        "The BREAKING-tagged entries are the answer for 4.2 -> 5.0: TTL "
        "default now 300s, purge() removed in favor of clear(), and the "
        "minimum Python raised to 3.10. Everything else in the notes is "
        "additive.",
    )
    b.answer(
        "fastcache 5.0 introduces three breaking changes relative to 4.2: "
        "the default TTL changed from never-expire to 300 seconds, "
        "cache.purge() was removed (use cache.clear()), and Python "
        "3.8/3.9 were dropped so 5.0 requires >=3.10. The new async API "
        "and LRU eviction stats are backward-compatible additions.",
        success=True,
    )
    return a.build(), b.build()


_TASK_BUILDERS = [
    t01_acme_revenue,
    t02_cve_libfoo,
    t03_saas_pricing,
    t04_rope_paper,
    t05_flight_duration,
    t06_bls_unemployment,
    t07_build_failure,
    t08_changelog_diff,
]


def build_all() -> list[dict]:
    """Build all 16 trajectories (8 tasks x 2 agents), a-then-b per task."""
    trajectories = []
    for builder_fn in _TASK_BUILDERS:
        traj_a, traj_b = builder_fn()
        trajectories.append(traj_a)
        trajectories.append(traj_b)
    return trajectories
