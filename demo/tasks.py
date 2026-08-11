"""Task definitions for the DeepCompare demo.

Eight research-style tasks, each with a stable id, the prompt given to both
agents, and the gold expected answer (used by the comparison engine to judge
outcomes).
"""

TASKS = [
    {
        "id": "t01_acme_revenue",
        "prompt": (
            "Find ACME Corp's total revenue for fiscal year 2025. "
            "Report a single dollar figure and say where it came from."
        ),
        "expected": "$4.82 billion (FY2025 results press release, investor relations)",
    },
    {
        "id": "t02_cve_libfoo",
        "prompt": (
            "What is the first released version of libfoo that fixes "
            "CVE-2025-1234?"
        ),
        "expected": "libfoo 2.14.1",
    },
    {
        "id": "t03_saas_pricing",
        "prompt": (
            "Compare DataHub Cloud's Pro and Team plans for a 25-seat team "
            "billed annually. Which plan is cheaper per year, and by how much?"
        ),
        "expected": (
            "Team ($11,700/yr for 25 seats) is cheaper than Pro ($14,700/yr) "
            "by $3,000 per year."
        ),
    },
    {
        "id": "t04_rope_paper",
        "prompt": (
            "Which paper first introduced Rotary Position Embedding (RoPE)? "
            "Give the title, first author, and year."
        ),
        "expected": (
            "\"RoFormer: Enhanced Transformer with Rotary Position Embedding\", "
            "Jianlin Su et al., 2021"
        ),
    },
    {
        "id": "t05_flight_duration",
        "prompt": (
            "A traveler flies SQ306 from Singapore (SIN) to London Heathrow "
            "(LHR), connects, then flies BA117 from LHR to New York (JFK), all "
            "on 10 June 2025. Scheduled times: SQ306 departs SIN 09:00 SGT, "
            "arrives LHR 15:40 BST; BA117 departs LHR 17:55 BST, arrives JFK "
            "20:45 EDT. What is the total elapsed journey time from SIN "
            "departure to JFK arrival?"
        ),
        "expected": "23 hours 45 minutes",
    },
    {
        "id": "t06_bls_unemployment",
        "prompt": (
            "According to the U.S. Bureau of Labor Statistics, what was the "
            "unemployment rate in December 2024?"
        ),
        "expected": "4.1 percent",
    },
    {
        "id": "t07_build_failure",
        "prompt": (
            "The widget-service CI build started failing right after PR #482 "
            "(\"chore: bump dependencies\") merged. Find the root cause and "
            "state the fix."
        ),
        "expected": (
            "PR #482 bumped pydantic 2.7.1 -> 3.0.0, which removed the "
            "v1-style `validator` import; migrate to `field_validator` "
            "(or pin pydantic<3)."
        ),
    },
    {
        "id": "t08_changelog_diff",
        "prompt": (
            "Summarize the breaking changes between fastcache 4.2 and "
            "fastcache 5.0."
        ),
        "expected": (
            "Three breaking changes: default TTL changed from never-expire to "
            "300 seconds; purge() was removed in favor of clear(); Python "
            "3.8/3.9 support dropped (requires >=3.10)."
        ),
    },
]

TASKS_BY_ID = {t["id"]: t for t in TASKS}
