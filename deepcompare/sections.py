"""The registry of sections: what attaches to a report, an aggregate or a
lineage, under which key, after what.

A section is one module that registers one function::

    from deepcompare import sections

    @sections.register("pair", "tools_profile", requires=("trust",))
    def tools_profile(report: dict) -> dict: ...

    @sections.register("aggregate", "rl", requires=("scorecard",))
    def rl(agg: dict, ctx: sections.AggregateContext) -> dict: ...

``scope`` is ``pair`` (attached to a pair report by ``report.compare``),
``aggregate`` (attached by ``suite.analyse_runs``) or ``lineage``
(attached by ``evolve``). A function that declares one positional
parameter is called with the target alone; one that declares two is also
handed the context of the scope (:class:`PairContext`,
:class:`AggregateContext`, :class:`LineageContext`) — the trajectories,
the golden tasks, whatever the target dict does not carry.

``requires`` names the keys that must be on the target before the section
runs: sections registered in this scope, or base keys the caller built
before attaching (``attribution`` on a pair report). Among registered
sections it is also the order: a section attaches after everything it
requires, and a section that must read the report *as it stands after*
another section names that section even when it reads none of its keys —
that is how today's attachment order is written down, and why the JSON key
order of a report does not depend on which module was imported first.
``after`` is the soft form: order before this one *if* it is attached in
the same pass, and nothing is required of it otherwise. Sections with no
ordering relation attach in registration order.

:func:`attach` runs the plan. A section that raises is caught and recorded
as ``unmeasurable("<ExceptionType>: <message>")`` under its key, and the
rest still attach — a new section can never take the report down. A
section whose ``requires`` is missing from the target is recorded the same
way, naming the key it needed. ``on_demand=True`` registers a section that
attaches only when named in ``attach(..., only=...)`` — a section whose
input arrives after the report is built (the golden tasks a
``milestones`` reading needs).
"""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from .section import unmeasurable

SCOPES = ("pair", "aggregate", "lineage")


@dataclass(frozen=True)
class Section:
    """One registered section: its key, the function, and its place."""
    scope: str
    key: str
    fn: Callable
    requires: tuple
    after: tuple
    on_demand: bool
    wants_ctx: bool
    index: int


@dataclass
class PairContext:
    """What a pair section may need beyond the report: the two trajectories
    (``None`` when the pass re-attaches from the report alone, as the
    milestones pass does), and the golden tasks and policy when known."""
    a: Any = None
    b: Any = None
    golden: Optional[dict] = None
    policy: Optional[dict] = None


@dataclass
class AggregateContext:
    """What an aggregate section may need beyond the aggregate dict: every
    trajectory, the runs grouped by task and side, the pair reports, the
    two agent names, the stability and reliability readings computed before
    the pairs were chosen, the golden tasks, policy, raw traces and
    family pattern the command was given, and ``extra`` for the rest."""
    trajectories: list = field(default_factory=list)
    runs_by_task: dict = field(default_factory=dict)
    reports: list = field(default_factory=list)
    names: tuple = ()
    stability: Optional[dict] = None
    reliability: Optional[dict] = None
    golden: Optional[dict] = None
    policy: Optional[dict] = None
    raws: Optional[dict] = None
    family_pattern: Optional[str] = None
    #: whatever else the command was given that a section reads
    #: (``token_cap`` for the budget section); empty when it was given nothing
    extra: dict = field(default_factory=dict)


@dataclass
class LineageContext:
    """What a lineage section may need beyond the lineage aggregate: the
    manifest, the generations in order, and whatever the lineage command
    reads that the aggregate does not carry (``extra``). The evolve command
    fills it when it adopts the registry."""
    lineage: Optional[dict] = None
    generations: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)


def _fresh() -> dict:
    return {scope: {} for scope in SCOPES}


_REGISTRY: dict = _fresh()
_COUNTER = [0]


def _wants_ctx(fn: Callable) -> bool:
    """True when ``fn`` can take the context as a second positional
    argument: two or more positional parameters, or ``*args``."""
    try:
        params = inspect.signature(fn).parameters.values()
    except (TypeError, ValueError):
        return True
    positional = 0
    for p in params:
        if p.kind is p.VAR_POSITIONAL:
            return True
        if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD):
            positional += 1
    return positional >= 2


def _names(what: str, key: str, names: Iterable[str]) -> tuple:
    out = tuple(names)
    for name in out:
        if not isinstance(name, str) or not name:
            raise ValueError(f"{what} of section {key!r} must name keys, got {name!r}")
        if name == key:
            raise ValueError(f"section {key!r} cannot {what.rstrip('s')} itself")
    return out


def register(scope: str, key: str, requires: Iterable[str] = (), after: Iterable[str] = (),
             on_demand: bool = False) -> Callable:
    """Register the decorated function as the section ``key`` of ``scope``.

    Raises at registration when the scope is unknown, the key is already
    taken by a different function, or ``requires``/``after`` are malformed
    (empty names, the section naming itself). The names themselves are
    resolved when the plan is built (:func:`check`, :func:`attach`), since
    a section may be registered before the module it requires is imported.
    """
    if scope not in SCOPES:
        raise ValueError(f"unknown section scope {scope!r}; one of {', '.join(SCOPES)}")
    if not isinstance(key, str) or not key:
        raise ValueError("a section key must be a non-empty string")
    req = _names("requires", key, requires)
    aft = _names("after", key, after)

    def decorate(fn: Callable) -> Callable:
        scoped = _REGISTRY[scope]
        existing = scoped.get(key)
        if existing is not None and (existing.fn.__module__, existing.fn.__qualname__) != (fn.__module__, fn.__qualname__):
            raise ValueError(f"section {key!r} is already registered in scope {scope!r} by "
                             f"{existing.fn.__module__}.{existing.fn.__qualname__}")
        index = existing.index if existing is not None else _COUNTER[0]
        if existing is None:
            _COUNTER[0] += 1
        scoped[key] = Section(scope, key, fn, req, aft, bool(on_demand), _wants_ctx(fn), index)
        return fn
    return decorate


def _scoped(scope: str) -> dict:
    if scope not in SCOPES:
        raise ValueError(f"unknown section scope {scope!r}; one of {', '.join(SCOPES)}")
    return _REGISTRY[scope]


def order(scope: str) -> list:
    """Every section of ``scope`` in attachment order: after everything it
    requires or is ``after``, ties by registration order. Raises
    ``ValueError`` on a cycle."""
    secs = sorted(_scoped(scope).values(), key=lambda s: s.index)
    keys = {s.key for s in secs}
    pending = {s.key: {d for d in s.requires + s.after if d in keys} for s in secs}
    out: list = []
    while pending:
        ready = next((s for s in secs if s.key in pending and not pending[s.key]), None)
        if ready is None:
            raise ValueError(f"sections of scope {scope!r} require each other in a cycle: "
                             + ", ".join(sorted(pending)))
        out.append(ready)
        del pending[ready.key]
        for deps in pending.values():
            deps.discard(ready.key)
    return out


def registered(scope: str) -> list:
    """The keys of ``scope`` in attachment order."""
    return [s.key for s in order(scope)]


def get(scope: str, key: str) -> Section:
    """The registered section, or ``KeyError``."""
    return _scoped(scope)[key]


def check(scope: str, base: Iterable[str] = ()) -> list:
    """Validate the scope's plan: every ``requires`` names a registered
    section or one of ``base`` (the keys the caller builds before
    attaching), and the graph has no cycle. Returns the order. Raises
    ``LookupError`` for an unknown name, ``ValueError`` for a cycle."""
    plan = order(scope)
    known = {s.key for s in plan} | set(base)
    for s in plan:
        unknown = [d for d in s.requires if d not in known]
        if unknown:
            raise LookupError(f"section {s.key!r} of scope {scope!r} requires "
                              f"{', '.join(repr(d) for d in unknown)}, which nothing provides")
    return plan


def plan(scope: str, only: Optional[Iterable[str]] = None) -> list:
    """The sections :func:`attach` would run: all but the on-demand ones,
    or exactly ``only`` (in attachment order). ``LookupError`` when
    ``only`` names a section that is not registered."""
    ordered = order(scope)
    if only is None:
        return [s for s in ordered if not s.on_demand]
    wanted = set(only)
    unknown = wanted - {s.key for s in ordered}
    if unknown:
        raise LookupError(f"no section {', '.join(sorted(repr(k) for k in unknown))} in scope {scope!r}")
    return [s for s in ordered if s.key in wanted]


def attach(scope: str, target: dict, ctx: Any = None, *, only: Optional[Iterable[str]] = None) -> dict:
    """Run the sections of ``scope`` on ``target`` in attachment order and
    place each result under its key.

    Every section runs, whatever the others did: one that raises is
    recorded as ``unmeasurable("<ExceptionType>: <message>")``, one whose
    ``requires`` is not on the target is recorded as unmeasurable naming
    the missing key. ``only`` restricts the pass to those keys — the way a
    caller re-attaches the sections that read a late input. Returns
    ``target``.
    """
    for s in plan(scope, only):
        missing = [d for d in s.requires if d not in target]
        if missing:
            target[s.key] = unmeasurable(f"requires {', '.join(missing)}, which is not on the {scope} target")
            continue
        try:
            target[s.key] = s.fn(target, ctx) if s.wants_ctx else s.fn(target)
        except Exception as exc:  # noqa: BLE001 — the point: one section cannot take the report down
            target[s.key] = unmeasurable(f"{type(exc).__name__}: {exc}")
    return target


@contextmanager
def isolated():
    """A registry with nothing in it, for the duration of the block — so a
    test can register sections without touching the real ones."""
    global _REGISTRY
    saved = _REGISTRY
    _REGISTRY = _fresh()
    try:
        yield _REGISTRY
    finally:
        _REGISTRY = saved


__all__ = ["SCOPES", "Section", "PairContext", "AggregateContext", "LineageContext",
           "register", "registered", "order", "plan", "check", "get", "attach", "isolated"]
