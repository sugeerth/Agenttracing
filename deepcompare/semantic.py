"""Semantic analysis for DeepCompare AI (SCHEMA.md v7) — meaning, not wording.

Two complementary approaches, both stdlib-only and deterministic:

- **TF-IDF cosine**: corpus-weighted similarity of paired step texts, with
  the vocabulary and document frequencies built over BOTH trajectories'
  steps (align.py word tokenization).  A large lexical-vs-semantic gap flags
  "same words, different meaning" (or the reverse).
- **Claim provenance**: typed, meaning-bearing facts (money, percents,
  durations, versions, CVEs, URLs, dates, unit-adjacent numbers) extracted
  by regex, deduplicated, and traced through the steps: which side carried
  which value, where it originated, whether it reached the final answer
  grounded in step outputs, whether corroboration was circular, and whether
  one agent contradicted itself.

Also classifies every step's process intent (frame / acquire / verify /
transform / decide / commit) and rolls a per-agent ``semantic_profile`` up
across a batch.
"""

from __future__ import annotations

import math
import re
from typing import Optional

from .align import _WORD_RE
from .trace import Step, Trajectory

CLAIM_KINDS = ("money", "percent", "duration", "version", "cve", "url", "date", "number")

SEMANTIC_BREAK_THRESHOLD = 0.5

_SOURCE_STEP_TYPES = frozenset({"search", "retrieve", "read"})

_VERIFY_CUES = (
    "double-check", "cross-check", "confirm", "corroborat", "validate",
    "verify", "settle", "reliable", "sanity",
)
_DECIDE_CUES = ("select", "choose", "pick", "open result")

_INTENT_ORDER = ("frame", "acquire", "verify", "transform", "decide", "commit")

# ---- claim extraction regexes ------------------------------------------

_MONEY_RE = re.compile(
    r"(\$)?[ \t]?(\d[\d,]*(?:\.\d+)?)[ \t]*(billion|million|thousand|bn|mn|[bmk])?\b"
    r"(?:\s*/\s*(?:yr|year|mo|month))?",
    re.IGNORECASE,
)
_MONEY_MULT = {
    "billion": 1e9, "bn": 1e9, "b": 1e9,
    "million": 1e6, "mn": 1e6, "m": 1e6,
    "thousand": 1e3, "k": 1e3,
}
#: single-letter multipliers ("$4.5B") only count when the $ sign is present.
_MONEY_LETTER_MULTS = frozenset({"b", "m", "k"})
_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent\b)", re.IGNORECASE)
_DURATION_HM_RE = re.compile(
    r"\b(\d+)\s*h(?:ours?|rs?)?(?:\s*(?:and\s+)?(\d+)\s*m(?:in(?:utes?)?)?)?\b",
    re.IGNORECASE,
)
_DURATION_M_RE = re.compile(r"\b(\d+)\s*min(?:utes?)?\b", re.IGNORECASE)
_VERSION_RE = re.compile(r"\bv?(\d+\.\d+\.\d+)\b")
_CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)
_FULL_URL_RE = re.compile(r"https?://([^/\s)\"',]+)", re.IGNORECASE)
_DOMAIN_RE = re.compile(
    r"\b((?:[a-z0-9-]+\.)+(?:com|org|net|gov|io|edu|dev))\b", re.IGNORECASE
)
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_PAT = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
_DATE_ISO_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_DATE_DMY_RE = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MONTH_PAT}\s+(\d{{4}})\b", re.IGNORECASE
)
_DATE_MDY_RE = re.compile(rf"\b{_MONTH_PAT}\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.IGNORECASE)
_DATE_MY_RE = re.compile(rf"\b{_MONTH_PAT}\s+(\d{{4}})\b", re.IGNORECASE)
_UNIT_WORDS = (
    "steps?", "tokens?", "sources?", "results?", "files?", "items?", "records?",
    "rows?", "errors?", "tests?", "pages?", "entries", "commits?", "releases?",
    "layovers?", "passengers?", "flights?", "points?",
)
_NUMBER_RE = re.compile(
    r"\b(\d[\d,]*(?:\.\d+)?)\s+(?:" + "|".join(_UNIT_WORDS) + r")\b", re.IGNORECASE
)


def _fmt_num(x: float) -> str:
    """Compact deterministic number string: 4.82e9, 1.2e10, 11700, 4.1."""
    if x >= 1e5:
        exp = int(math.floor(math.log10(x)))
        mantissa = x / (10 ** exp)
        m = f"{mantissa:g}"
        return f"{m}e{exp}"
    if x == int(x):
        return str(int(x))
    return f"{x:g}"


def _num(text: str) -> float:
    return float(text.replace(",", ""))


def extract_from_text(text: str) -> list[tuple[str, str, str]]:
    """Extract (kind, value, normalized) claim tuples from one text.

    Deterministic, in left-to-right order per kind, kinds in CLAIM_KINDS
    order.  Bare numbers are only claimed when adjacent to a unit-ish word.
    """
    found: list[tuple[str, str, str]] = []
    if not text:
        return found

    for m in _MONEY_RE.finditer(text):
        dollar, num, mult = m.group(1), m.group(2), m.group(3)
        if mult and mult.lower() in _MONEY_LETTER_MULTS and not dollar:
            mult = None  # "45m" without $ is not money (probably a duration)
        if not dollar and not mult:
            continue
        value = m.group(0).strip()
        x = _num(num) * (_MONEY_MULT[mult.lower()] if mult else 1.0)
        found.append(("money", value, _fmt_num(x)))

    for m in _PERCENT_RE.finditer(text):
        found.append(("percent", m.group(0).strip(), f"{float(m.group(1)):g}"))

    consumed: list[tuple[int, int]] = []
    for m in _DURATION_HM_RE.finditer(text):
        hours, minutes = int(m.group(1)), int(m.group(2) or 0)
        found.append(("duration", m.group(0).strip(), str(hours * 60 + minutes)))
        consumed.append(m.span())
    for m in _DURATION_M_RE.finditer(text):
        if any(s <= m.start() and m.end() <= e for s, e in consumed):
            continue
        found.append(("duration", m.group(0).strip(), str(int(m.group(1)))))

    for m in _VERSION_RE.finditer(text):
        found.append(("version", m.group(0).strip(), m.group(1)))

    for m in _CVE_RE.finditer(text):
        found.append(("cve", m.group(0), m.group(0).upper()))

    seen_domains: list[tuple[int, int]] = []
    for m in _FULL_URL_RE.finditer(text):
        found.append(("url", m.group(1).lower(), m.group(1).lower()))
        seen_domains.append(m.span())
    for m in _DOMAIN_RE.finditer(text):
        if any(s <= m.start() and m.end() <= e for s, e in seen_domains):
            continue
        found.append(("url", m.group(1).lower(), m.group(1).lower()))

    date_spans: list[tuple[int, int]] = []
    for m in _DATE_ISO_RE.finditer(text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        found.append(("date", m.group(0), f"{y:04d}-{mo:02d}-{d:02d}"))
        date_spans.append(m.span())
    for m in _DATE_DMY_RE.finditer(text):
        d, mo, y = int(m.group(1)), _MONTHS[m.group(2).lower()], int(m.group(3))
        found.append(("date", m.group(0), f"{y:04d}-{mo:02d}-{d:02d}"))
        date_spans.append(m.span())
    for m in _DATE_MDY_RE.finditer(text):
        mo, d, y = _MONTHS[m.group(1).lower()], int(m.group(2)), int(m.group(3))
        found.append(("date", m.group(0), f"{y:04d}-{mo:02d}-{d:02d}"))
        date_spans.append(m.span())
    for m in _DATE_MY_RE.finditer(text):
        if any(not (m.end() <= s or e <= m.start()) for s, e in date_spans):
            continue
        mo, y = _MONTHS[m.group(1).lower()], int(m.group(2))
        found.append(("date", m.group(0), f"{y:04d}-{mo:02d}"))

    for m in _NUMBER_RE.finditer(text):
        x = _num(m.group(1))
        if x == int(x) and 1900 <= x <= 2100:
            continue  # year-like: too noisy to claim as a bare number
        found.append(("number", m.group(0).strip(), _fmt_num(x)))

    return found


def _first_domain(text: str) -> Optional[str]:
    m = _FULL_URL_RE.search(text)
    if m:
        return m.group(1).lower()
    m = _DOMAIN_RE.search(text)
    return m.group(1).lower() if m else None


# ---- TF-IDF cosine ------------------------------------------------------


def _step_text(step: Step) -> str:
    return f"{step.name} {step.input} {step.output}"


def _tfidf_rows(alignment: list[dict], a: Trajectory, b: Trajectory) -> tuple[list[dict], Optional[int]]:
    """Per-row lexical vs TF-IDF-cosine semantic similarity."""
    docs = [_WORD_RE.findall(_step_text(s).lower()) for s in a.steps] + [
        _WORD_RE.findall(_step_text(s).lower()) for s in b.steps
    ]
    n_docs = len(docs)
    df: dict[str, int] = {}
    for tokens in docs:
        for term in set(tokens):
            df[term] = df.get(term, 0) + 1
    idf = {t: math.log((n_docs + 1) / (c + 1)) + 1.0 for t, c in df.items()}

    def vector(doc_index: int) -> dict[str, float]:
        tf: dict[str, int] = {}
        for term in docs[doc_index]:
            tf[term] = tf.get(term, 0) + 1
        return {t: c * idf[t] for t, c in tf.items()}

    def cosine(u: dict[str, float], v: dict[str, float]) -> float:
        if not u and not v:
            return 1.0
        dot = sum(w * v[t] for t, w in u.items() if t in v)
        nu = math.sqrt(sum(w * w for w in u.values()))
        nv = math.sqrt(sum(w * w for w in v.values()))
        if nu == 0.0 or nv == 0.0:
            return 0.0
        return dot / (nu * nv)

    rows: list[dict] = []
    first_break: Optional[int] = None
    for row_idx, entry in enumerate(alignment):
        if entry["a_index"] is None or entry["b_index"] is None:
            continue
        sem = round(
            cosine(vector(entry["a_index"]), vector(len(a.steps) + entry["b_index"])), 4
        )
        rows.append(
            {
                "row": row_idx,
                "a_index": entry["a_index"],
                "b_index": entry["b_index"],
                "lexical": entry["similarity"],
                "semantic": sem,
            }
        )
        if first_break is None and sem < SEMANTIC_BREAK_THRESHOLD:
            first_break = row_idx
    return rows, first_break


# ---- intents ------------------------------------------------------------


def _step_intent(step: Step) -> str:
    """Classify one step's process intent from its type plus text cues."""
    cue_text = f"{step.name} {step.input}".lower()
    has_verify = any(cue in cue_text for cue in _VERIFY_CUES)
    if step.type == "plan":
        return "frame"
    if step.type == "answer":
        return "commit"
    if step.type == "tool_call":
        return "verify" if has_verify else "transform"
    if step.type == "reason":
        return "verify" if has_verify else "decide"
    # search / retrieve / read
    if has_verify:
        return "verify"
    if step.type == "retrieve" and any(cue in cue_text for cue in _DECIDE_CUES):
        return "decide"
    return "acquire"


# ---- claim bookkeeping --------------------------------------------------


class _Claim:
    """Internal mutable claim record (serialized at the end)."""

    __slots__ = (
        "cid", "kind", "value", "normalized", "steps", "output_steps", "in_answer"
    )

    def __init__(self, cid: str, kind: str, value: str, normalized: str):
        self.cid = cid
        self.kind = kind
        self.value = value
        self.normalized = normalized
        self.steps: dict[str, set[int]] = {"a": set(), "b": set()}
        self.output_steps: dict[str, set[int]] = {"a": set(), "b": set()}
        self.in_answer: dict[str, bool] = {"a": False, "b": False}


def _collect_claims(a: Trajectory, b: Trajectory) -> list[_Claim]:
    claims: dict[tuple[str, str], _Claim] = {}
    order: list[_Claim] = []

    def note(kind: str, value: str, normalized: str) -> _Claim:
        key = (kind, normalized)
        claim = claims.get(key)
        if claim is None:
            claim = _Claim(f"c{len(order) + 1}", kind, value, normalized)
            claims[key] = claim
            order.append(claim)
        return claim

    for side, traj in (("a", a), ("b", b)):
        for idx, step in enumerate(traj.steps):
            for kind, value, normalized in extract_from_text(step.input):
                note(kind, value, normalized).steps[side].add(idx)
            for kind, value, normalized in extract_from_text(step.output):
                claim = note(kind, value, normalized)
                claim.steps[side].add(idx)
                claim.output_steps[side].add(idx)
        for kind, value, normalized in extract_from_text(traj.outcome.answer):
            claim = note(kind, value, normalized)
            claim.steps[side].add(len(traj.steps) - 1)
            claim.in_answer[side] = True
    return order


def _origin(claim: _Claim, a: Trajectory, b: Trajectory) -> dict:
    """Earliest carrying step (tie broken toward agent a), with URL source."""
    best_side, best_idx = None, None
    for side in ("a", "b"):
        if claim.steps[side]:
            idx = min(claim.steps[side])
            if best_idx is None or idx < best_idx:
                best_side, best_idx = side, idx
    assert best_side is not None and best_idx is not None
    traj = a if best_side == "a" else b
    step = traj.steps[best_idx] if 0 <= best_idx < len(traj.steps) else None
    source = None
    if step is not None:
        source = _first_domain(step.output) or _first_domain(step.input)
    return {"agent": best_side, "step": best_idx, "source": source}


def _side_source(claim: _Claim, side: str, traj: Trajectory) -> Optional[str]:
    """First extractable URL domain among one side's carrying steps."""
    for idx in sorted(claim.steps[side]):
        if 0 <= idx < len(traj.steps):
            step = traj.steps[idx]
            domain = _first_domain(step.output) or _first_domain(step.input)
            if domain:
                return domain
    return None


def _cite_snippet(text: str, domain: str, limit: int = 80) -> str:
    """Snippet of ``text`` around the mention of ``domain`` (<= limit chars)."""
    flat = " ".join(text.split())
    pos = flat.lower().find(domain.lower())
    if pos < 0:
        return flat[:limit]
    start = max(0, pos - (limit - len(domain)) // 2)
    frag = flat[start : start + limit]
    if start > 0:
        frag = "…" + frag
    if start + limit < len(flat):
        frag = frag + "…"
    return frag


# ---- main entry ---------------------------------------------------------


def semantic_analysis(report: dict, a: Trajectory, b: Trajectory) -> dict:
    """Build the SCHEMA.md v7 ``semantic`` object for a comparison report."""
    alignment = report["alignment"]
    names = {"a": a.agent.name, "b": b.agent.name}
    trajs = {"a": a, "b": b}

    rows, first_break = _tfidf_rows(alignment, a, b)
    claims = _collect_claims(a, b)

    # matches_expected against claims found in the gold answer.
    expected_claims: dict[str, list[tuple[str, str]]] = {}
    if a.task.expected:
        for kind, value, normalized in extract_from_text(a.task.expected):
            expected_claims.setdefault(kind, []).append((value, normalized))

    claims_json: list[dict] = []
    for claim in claims:
        matches: Optional[bool] = None
        if claim.kind in expected_claims:
            matches = claim.normalized in {n for _, n in expected_claims[claim.kind]}
        claims_json.append(
            {
                "id": claim.cid,
                "kind": claim.kind,
                "value": claim.value,
                "normalized": claim.normalized,
                "matches_expected": matches,
                "a_steps": sorted(claim.steps["a"]),
                "b_steps": sorted(claim.steps["b"]),
                "origin": _origin(claim, a, b),
            }
        )

    # Conflicts: same-kind, different values carried into the two answers.
    conflicts: list[dict] = []
    for kind in CLAIM_KINDS:
        a_ans = [c for c in claims if c.kind == kind and c.in_answer["a"]]
        b_ans = [c for c in claims if c.kind == kind and c.in_answer["b"]]
        b_norms = {c.normalized for c in b_ans}
        a_norms = {c.normalized for c in a_ans}
        a_only = [c for c in a_ans if c.normalized not in b_norms]
        b_only = [c for c in b_ans if c.normalized not in a_norms]
        if not a_only or not b_only:
            continue
        ca, cb = a_only[0], b_only[0]
        if kind == "url":
            part_a = f"A cited {ca.value}"
            part_b = f"B cited {cb.value}"
        else:
            src_a = _side_source(ca, "a", a)
            src_b = _side_source(cb, "b", b)
            part_a = f"A carried {ca.value}" + (f" (from {src_a})" if src_a else "")
            part_b = f"B carried {cb.value}" + (f" (from {src_b})" if src_b else "")
        summary = f"{part_a}; {part_b}"
        if kind in expected_claims:
            summary += f"; expected: {expected_claims[kind][0][0]}."
        else:
            summary += "."
        conflicts.append(
            {"kind": kind, "a_claim": ca.cid, "b_claim": cb.cid, "summary": summary}
        )

    # Intents.
    intents = {
        side: [
            {"step": i, "intent": _step_intent(step)}
            for i, step in enumerate(trajs[side].steps)
        ]
        for side in ("a", "b")
    }
    present = {
        side: {entry["intent"] for entry in intents[side]} for side in ("a", "b")
    }
    missing = {
        side: [
            intent
            for intent in _INTENT_ORDER
            if intent in present["b" if side == "a" else "a"]
            and intent not in present[side]
        ]
        for side in ("a", "b")
    }

    # Grounding: answer claims traced to earlier step outputs of the same side.
    grounding: dict[str, dict] = {}
    for side in ("a", "b"):
        last_idx = len(trajs[side].steps) - 1
        answer_claims = [c for c in claims if c.in_answer[side]]
        ungrounded = []
        grounded = 0
        for claim in answer_claims:
            if any(i < last_idx for i in claim.output_steps[side]):
                grounded += 1
            else:
                ungrounded.append({"claim": claim.cid, "value": claim.value})
        total = len(answer_claims)
        grounding[side] = {
            "claims_total": total,
            "claims_grounded": grounded,
            "score": round(grounded / total, 4) if total else 1.0,
            "ungrounded": ungrounded,
        }

    # Independence: multi-source answer claims checked for circular citation.
    independence: list[dict] = []
    for side in ("a", "b"):
        traj = trajs[side]
        for claim in claims:
            if not claim.in_answer[side] or claim.kind == "url":
                continue
            carriers = [
                i
                for i in sorted(claim.steps[side])
                if 0 <= i < len(traj.steps) and traj.steps[i].type in _SOURCE_STEP_TYPES
            ]
            step_domains = []
            for i in carriers:
                step = traj.steps[i]
                domain = _first_domain(step.output) or _first_domain(step.input)
                if domain:
                    step_domains.append((i, domain))
            if len(step_domains) < 2:
                continue
            sources: list[str] = []
            for _i, domain in step_domains:
                if domain not in sources:
                    sources.append(domain)
            if len(sources) < 2:
                continue  # one domain quoted twice is not corroboration
            circular = False
            evidence = "no cross-citation between sources detected"
            for pos, (later_i, later_domain) in enumerate(step_domains):
                text = f"{traj.steps[later_i].input} {traj.steps[later_i].output}"
                for earlier_i, earlier_domain in step_domains[:pos]:
                    if earlier_domain != later_domain and earlier_domain in text.lower():
                        circular = True
                        evidence = (
                            f'the corroborating source\'s text cites {earlier_domain} '
                            f'itself: "{_cite_snippet(text, earlier_domain)}"'
                        )
                        break
                if circular:
                    break
            independence.append(
                {
                    "claim": claim.cid,
                    "agent": side,
                    "sources": sources,
                    "circular": circular,
                    "evidence": evidence,
                }
            )

    # Contradictions: conflicting asserted values inside one trajectory.
    contradictions: list[dict] = []
    for side in ("a", "b"):
        traj = trajs[side]
        for kind in CLAIM_KINDS:
            if kind == "url":
                continue  # citing two different sources is not a contradiction
            group = [c for c in claims if c.kind == kind and c.steps[side]]
            asserted: list[tuple[_Claim, list[int]]] = []
            for claim in group:
                assert_steps = [
                    i
                    for i in sorted(claim.steps[side])
                    if 0 <= i < len(traj.steps)
                    and traj.steps[i].type in ("reason", "answer")
                ]
                noted = any(
                    0 <= i < len(traj.steps)
                    and traj.steps[i].note
                    and "conflict" in traj.steps[i].note.lower()
                    for i in claim.steps[side]
                )
                if claim.in_answer[side] and (len(traj.steps) - 1) not in assert_steps:
                    assert_steps.append(len(traj.steps) - 1)
                if assert_steps or noted:
                    asserted.append((claim, sorted(set(assert_steps))))
            # Distinct by mantissa: "$4.5" quoted next to "$4.5 billion" is a
            # scale-dropped mention, not a genuine self-contradiction.
            distinct = {c.normalized.split("e")[0] for c, _ in asserted}
            if len(distinct) < 2:
                continue
            seen_mantissas: set[str] = set()
            first_two = []
            for claim, idxs in asserted:
                mantissa = claim.normalized.split("e")[0]
                if mantissa in seen_mantissas:
                    continue
                seen_mantissas.add(mantissa)
                first_two.append((claim, idxs))
                if len(first_two) == 2:
                    break
            if len(first_two) < 2:
                continue
            # Values co-mentioned in the same step are a comparison (price
            # tiers, leg durations, affected-vs-fixed versions), not a
            # contradiction the agent failed to notice.
            if first_two[0][0].steps[side] & first_two[1][0].steps[side]:
                continue
            steps_union = sorted({i for _, idxs in first_two for i in idxs})
            values = [c.value for c, _ in first_two]
            contradictions.append(
                {
                    "agent": side,
                    "steps": steps_union,
                    "kind": kind,
                    "values": values,
                    "summary": (
                        f"{names[side]} carried conflicting {kind} values within "
                        f"its own trajectory ({values[0]} vs {values[1]})"
                    ),
                }
            )

    # Narrative.
    sentences: list[str] = []
    if first_break is not None:
        row = next(r for r in rows if r["row"] == first_break)
        sentences.append(
            f"Meaning breaks from wording at alignment row {first_break} "
            f"(lexical {row['lexical']:.2f} vs semantic {row['semantic']:.2f})."
        )
    elif rows:
        gap_row = max(rows, key=lambda r: (r["lexical"] - r["semantic"], -r["row"]))
        if gap_row["lexical"] - gap_row["semantic"] > 0.3:
            sentences.append(
                f"Row {gap_row['row']} shows similar wording but drifting meaning "
                f"(lexical {gap_row['lexical']:.2f} vs semantic {gap_row['semantic']:.2f})."
            )
        else:
            sentences.append(
                "Lexical and semantic similarity track closely across all aligned rows."
            )
    else:
        sentences.append("No aligned step pairs to compare semantically.")
    if conflicts:
        sentences.append(f"Decisive claim conflict ({conflicts[0]['kind']}): "
                         f"{conflicts[0]['summary']}")
    process_bits: list[str] = []
    for side in ("a", "b"):
        if "verify" in missing[side]:
            bit = f"{names[side]} never verified its evidence"
            circ = next(
                (e for e in independence if e["agent"] == side and e["circular"]), None
            )
            if circ:
                bit += (
                    f", and its corroboration was circular "
                    f"({' cites '.join(circ['sources'][:2][::-1])})"
                )
            process_bits.append(bit)
        else:
            circ = next(
                (e for e in independence if e["agent"] == side and e["circular"]), None
            )
            if circ:
                process_bits.append(
                    f"{names[side]}'s corroboration was circular "
                    f"({circ['sources'][0]} echoed by {circ['sources'][1]})"
                )
    for contradiction in contradictions[:1]:
        process_bits.append(contradiction["summary"])
    if process_bits:
        sentences.append("; ".join(process_bits[:2]) + ".")

    return {
        "methods": ["tfidf_cosine", "claim_provenance"],
        "rows": rows,
        "first_semantic_break": first_break,
        "claims": claims_json,
        "conflicts": conflicts,
        "intents": {"a": intents["a"], "b": intents["b"], "missing": missing},
        "grounding": grounding,
        "independence": independence,
        "contradictions": contradictions,
        "narrative": " ".join(sentences[:3]),
    }


def semantic_profile(reports: list[dict]) -> dict:
    """Aggregate per-agent semantic profile across a batch of reports."""
    if not reports:
        return {}
    names = {
        "a": reports[0]["a"]["agent"]["name"],
        "b": reports[0]["b"]["agent"]["name"],
    }
    profile: dict[str, dict] = {}
    for side in ("a", "b"):
        verified = 0
        scores: list[float] = []
        circular = 0
        contra = 0
        counted = 0
        for report in reports:
            semantic = report.get("semantic")
            if not semantic:
                continue
            counted += 1
            if any(e["intent"] == "verify" for e in semantic["intents"][side]):
                verified += 1
            scores.append(semantic["grounding"][side]["score"])
            circular += sum(
                1 for e in semantic["independence"]
                if e["agent"] == side and e["circular"]
            )
            contra += sum(
                1 for e in semantic["contradictions"] if e["agent"] == side
            )
        profile[side] = {
            "verification_rate": round(verified / counted, 4) if counted else 0.0,
            "grounding": round(sum(scores) / len(scores), 4) if scores else 1.0,
            "circular_incidents": circular,
            "contradictions": contra,
        }
    pa, pb = profile["a"], profile["b"]
    narrative = (
        f"{names['a']} verifies on {pa['verification_rate']:.0%} of tasks with "
        f"grounding {pa['grounding']:.2f}, versus {names['b']} at "
        f"{pb['verification_rate']:.0%} and {pb['grounding']:.2f}. "
        f"{names['b']} logged {pb['circular_incidents']} circular-corroboration "
        f"incident(s) and {pb['contradictions']} internal contradiction(s), "
        f"versus {pa['circular_incidents']} and {pa['contradictions']} for "
        f"{names['a']}."
    )
    profile["narrative"] = narrative
    return profile
