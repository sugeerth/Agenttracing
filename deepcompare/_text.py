"""The prose of a reading, in one place.

Every section ends in a narrative, and every narrative spells the same
things: a number with a real minus sign, a signed delta, a percentage, a
duration, a count with its noun, a list of names, an interval, the name
of a side. These helpers are those spellings, moved here from the
sections that first wrote them so that a grammar fix lands everywhere at
once.

Two sections did not always spell the same thing the same way. Where they
differ, both spellings are kept behind a parameter — ``num(..., trim=)``,
``pct(..., style=)``, ``secs(..., whole_above=, tenths_above=)`` — so that
no narrative changed by a byte when the helpers moved. The defaults are
the spelling most sections use; a later, deliberate unification can drop
the alternatives.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Optional

#: the dash a missing number is spelled with
NONE = "—"


def num(v: Optional[float], places: int = 2, *, trim: bool = True, none: str = NONE) -> str:
    """A number as text: integers plain, otherwise ``places`` decimals,
    with a real minus sign; ``none`` when there is no number.

    ``trim`` drops trailing zeros (``1.50`` → ``1.5``), which is how the RL,
    stats and space sections spell a score; the audit section keeps the
    zeros (``trim=False``), so its ``1.50`` stays ``1.50``.
    """
    if v is None:
        return none
    if abs(v - round(v)) < 1e-9:
        text = f"{int(round(v))}"
    else:
        text = f"{v:.{places}f}"
        if trim:
            text = text.rstrip("0").rstrip(".")
    return text.replace("-", "−")


def signed(v: Optional[float], places: int = 2, *, trim: bool = True, none: str = NONE) -> str:
    """``num`` with an explicit sign: ``+3``, ``−0.5``, ``+0``."""
    if v is None:
        return none
    text = num(v, places, trim=trim, none=none)
    return text if v < 0 else f"+{text}"


def pct(p: Optional[float], *, style: str = "round", none: str = NONE) -> str:
    """A share in [0, 1] as a whole-number percentage.

    ``style="round"`` rounds ``100 * p`` with Python's ``round`` (half to
    even on the exact product), the spelling the stats section uses;
    ``style="format"`` is ``f"{p:.0%}"``, the trust section's spelling. The
    two differ on some halves (``0.005`` → ``0%`` against ``1%``), which is
    why both are kept.
    """
    if p is None:
        return none
    if style == "format":
        return f"{p:.0%}"
    if style != "round":
        raise ValueError(f"unknown pct style {style!r}")
    return f"{round(100 * p):.0f}%"


def secs(v: float, *, whole_above: Optional[float] = 10, tenths_above: Optional[float] = 1) -> str:
    """Seconds as text with a unit: ``12s``, ``3.4s``, ``0.25s``.

    Whole seconds at or above ``whole_above``, tenths at or above
    ``tenths_above``, hundredths below; ``None`` switches a tier off. The
    defaults are the impact section's three tiers. ``tenths_above=None``
    never shows hundredths (tool profile, milestones, impact's cluster
    scores); ``whole_above=None`` never drops the tenths (timing, horizon).
    """
    if whole_above is not None and v >= whole_above:
        return f"{v:.0f}s"
    if tenths_above is None or v >= tenths_above:
        return f"{v:.1f}s"
    return f"{v:.2f}s"


def plural(n: int, word: str, plural: Optional[str] = None) -> str:
    """``1 run``, ``3 runs``; an irregular plural is given explicitly."""
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def join_names(names: Iterable[str], conj: str = "and") -> str:
    """``a``, ``a and b``, ``a, b and c`` — no serial comma, the way the
    narratives list things."""
    items = [str(n) for n in names]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + f" {conj} " + items[-1]


def interval(point: Optional[float], lo: Optional[float], hi: Optional[float],
             fmt: Callable[[Any], str] = num) -> str:
    """``5.23 [1.71, 8.75]`` — a point with its interval, each spelled by
    ``fmt`` (``num`` by default, ``pct`` for a probability)."""
    return f"{fmt(point)} [{fmt(lo)}, {fmt(hi)}]"


def side_name(report: Any, side: str, default: Optional[str] = None) -> str:
    """The agent name of ``report[side]``, else ``default`` (the side letter
    when none is given)."""
    run = report.get(side) if isinstance(report, dict) else None
    agent = run.get("agent") if isinstance(run, dict) else None
    name = agent.get("name") if isinstance(agent, dict) else None
    return str(name or (side if default is None else default))


def run_name(run: Any, default: str = "the run") -> str:
    """The agent name of one run — a report side dict or a Trajectory —
    else ``default``."""
    if isinstance(run, dict):
        return str(((run.get("agent") or {}).get("name")) or default)
    agent = getattr(run, "agent", None)
    return str(getattr(agent, "name", None) or default)


__all__ = ["NONE", "num", "signed", "pct", "secs", "plural", "join_names", "interval", "side_name", "run_name"]
