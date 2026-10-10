"""One confidence vocabulary for every output.

Diagnosis, reading, recommendations, the verdict card and the page all
quote the same shape, so a reader never meets "high confidence" beside
"n=1" in two blocks of the same report:

``{"level": low|medium|high, "n": <int|None>, "basis": <why>,
   "verified": hypothesized|replay-verified|replay-refuted|replay-mixed|n/a}``

``level`` is set by counts and evidence class, never by tone; ``n`` is
the number of independent observations behind it (pairs, runs, tasks);
``verified`` says whether a replay has tested the claim.  A single
observation can never be "high": one comparison cannot rule out luck.
"""

from __future__ import annotations

from typing import Optional

LEVELS = ("low", "medium", "high")
VERIFICATIONS = ("hypothesized", "replay-verified", "replay-refuted",
                 "replay-mixed", "n/a")


def confidence(level: str, n: Optional[int], basis: str,
               verified: str = "n/a") -> dict:
    """Build the shared confidence object, enforcing the n=1 ceiling."""
    if level not in LEVELS:
        raise ValueError(f"unknown confidence level {level!r}")
    if verified not in VERIFICATIONS:
        raise ValueError(f"unknown verification state {verified!r}")
    if n is not None and n <= 1 and level == "high":
        level = "medium"
        basis = (basis + "; capped at medium: a single observation cannot "
                 "rule out luck")
    return {"level": level, "n": n, "basis": basis, "verified": verified}


def describe(conf: dict) -> str:
    """``medium (n=1) — basis; hypothesized`` for prose and cards."""
    if not conf or not conf.get("level"):
        return "no confidence stated"
    text = conf["level"]
    if conf.get("n") is not None:
        text += f" (n={conf['n']})"
    if conf.get("basis"):
        text += f" — {conf['basis']}"
    if conf.get("verified") and conf["verified"] != "n/a":
        text += f"; {conf['verified']}"
    return text
