"""What a policy *does*, and where two policies part: a behaviour space over episodes.

The RL section (:mod:`deepcompare.rl`) says which policy earned more. It
does not say what either one *did*. This module reads the same episodes
as behaviour: each step becomes one token — the tool's name for a
tool-ish step, the step's own family (``plan``, ``reason``, ``answer``)
otherwise — so an episode is a short string of tokens in the order the
policy acted, and a policy is the set of those strings. Four readings of
that set, all counts over recorded steps:

* **The vocabulary.** Every token either policy ever emits, its count and
  its share of that policy's steps, and the *signature*: the tokens one
  policy uses and the other never does. A token can be a tool's name or a
  step family, so a tool literally named ``reason`` would share a token
  with the reason family; the vocabulary lists what the traces contain,
  it does not namespace them.

* **The trie.** A prefix tree over the token streams. Every node carries
  its prefix, how many episodes of each policy pass through it, and the
  mean return and success rate of the episodes below it. Two prunings
  keep it readable and both are reported in ``trie["pruned"]``: a subtree
  reached by a single episode is kept as one leaf carrying ``tail`` (the
  tokens folded under it) instead of being expanded, and nothing is built
  below ``max_depth`` (such a node carries ``truncated``). The **branch
  points** are the nodes where the two policies' traffic splits most
  unevenly: at a node, each policy's episodes divide over the children as
  a distribution, and the imbalance is the total-variation distance
  between those two distributions (0 = the policies split alike, 1 = they
  take disjoint children). The score weights that by how many episodes
  reach the node (``imbalance × episodes_here / episodes``), so a
  lopsided split three episodes deep does not outrank the place the whole
  batch divides. Each branch point names the child each policy leans to
  and the mean return below it — the return on each side.

* **N-grams.** The commonest length-2 and length-3 token sequences per
  policy, and the ones over-represented in the episodes that succeeded
  against the ones that failed: the ratio of the two *rates* (a gram's
  share of all grams of that length in the group), with all four counts
  carried so a reader can see that a ratio of 3.0 may be 3 against 1. A
  gram absent from the losing episodes has no ratio (``None``) and is
  listed first; nothing is smoothed, and a gram under ``min_count``
  occurrences is not listed at all. The rate's denominator is the group's
  own gram count, so a winning episode that is simply shorter raises the
  rate of everything it does — ``per_episode`` is carried beside it for
  that reason, and the block says so. Both ends are returned: ``top`` is
  the winners' habits, ``bottom`` the losers' (a retry loop lives there,
  not among the winners).

* **Distance.** Normalised edit distance over the token streams:
  Levenshtein / the longer stream's length, so it is 0..1. Edit distance
  and not Jaccard over n-grams because order and length are the whole
  point here — a policy that does the same work in ten steps instead of
  twenty, or that searches before it reads rather than after, differs in
  a way a bag of n-grams cannot see, and insertion/deletion is exactly
  the shape of "one extra search". From the matrix: each policy's
  **behavioural spread** (the mean distance between two of its own
  episodes — a policy that always does the same thing has a small spread,
  which no return number carries), the mean distance between the
  policies, and each episode's nearest neighbour, so an episode sitting
  inside the other policy's cloud can be found.

  The matrix is quadratic in episodes; the distance itself would be
  quadratic in tokens, but Myers' bit-parallel algorithm holds the DP's
  columns as bit vectors and makes it linear in the text over a few
  machine words (checked against the textbook row DP in the tests).
  Both are still capped: at most
  ``MAX_DISTANCE_EPISODES`` (120) episodes, chosen round-robin over the
  policies in ``(task, run)`` order so both are represented, over at most
  ``MAX_DISTANCE_TOKENS`` (200) tokens each. Past the cap the extra
  episodes are simply not in the matrix, the spread or the layout — they
  are not estimated from the ones that are — and ``distance["capped"]``,
  ``counted``, ``of`` and ``note`` say exactly that, so the page can too.
  At the cap the whole section costs about two seconds; the 96-episode
  training demo fits inside it whole.

* **The layout.** Classical multidimensional scaling of that matrix in
  pure Python: double-centre the squared distances, then the top two
  eigenvectors by power iteration from a fixed seed for a fixed number of
  iterations, with each eigenvector's sign fixed by its largest-magnitude
  entry so the same input gives the same bytes. **The axes mean nothing**
  — not steps, not return, not time. Only relative position carries
  information, and only as far as MDS can embed the distances in a plane:
  two marks close together behaved alike, two far apart did not.

Guards: one episode, identical episodes, a policy with no episodes and an
empty vocabulary all return a block that says ``measurable: False`` with
a reason on the part that cannot be computed, never a fabricated number.
"""

from __future__ import annotations

import math
from typing import Optional

from .impact import TOOLISH

VERSION = 1
#: no trie node is built below this prefix depth
MAX_DEPTH = 24
#: a subtree reached by fewer episodes than this is folded into one leaf
MIN_TAIL_EPISODES = 2
#: at most this many episodes enter the distance matrix and the layout
MAX_DISTANCE_EPISODES = 120
#: at most this many tokens of an episode enter a distance
MAX_DISTANCE_TOKENS = 200
#: the full matrix is carried in the output only up to this many episodes;
#: past it every number read off it still is, but N² numbers are not
MATRIX_JSON_EPISODES = 40
NGRAM_SIZES = (2, 3)
#: n-grams listed per policy, and per side of the winning/losing ratio
TOP_NGRAMS = 8
#: a gram under this many occurrences among the winners is not ranked
MIN_NGRAM_COUNT = 2
#: branch points returned, most divergent first
TOP_BRANCHES = 6
POWER_ITERS = 200
#: the fixed seed of the MDS start vector (an LCG, so it cannot drift)
MDS_SEED = 12345


# ---------------------------------------------------------------- formatting

def _num(v: Optional[float]) -> str:
    """A return as text: integers plain, else up to two decimals, real minus."""
    if v is None:
        return "—"
    text = f"{int(round(v))}" if abs(v - round(v)) < 1e-9 else f"{v:.2f}".rstrip("0").rstrip(".")
    return text.replace("-", "−")


def _plural(n: int, word: str, plural: Optional[str] = None) -> str:
    return f"{n} {word if n == 1 else (plural or word + 's')}"


def _mean(values: list) -> Optional[float]:
    vals = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return round(sum(vals) / len(vals), 4) if vals else None


# ---------------------------------------------------------------- tokens

def step_token(kind: Optional[str], name: Optional[str]) -> str:
    """The token one step contributes: the tool's name when the step is
    tool-ish (and names itself), else the step's family."""
    kind = str(kind or "").strip() or "step"
    name = str(name or "").strip()
    return name if (kind in TOOLISH and name) else kind


def episode_tokens(steps: list) -> list:
    """The token stream of a list of steps — ``Step`` objects or step dicts."""
    out: list = []
    for st in steps or []:
        if isinstance(st, dict):
            out.append(step_token(st.get("type"), st.get("name")))
        else:
            out.append(step_token(getattr(st, "type", None), getattr(st, "name", None)))
    return out


def behaviour_episodes(trajectories: list, agents: Optional[dict] = None) -> list:
    """The behaviour episodes of a runs batch: one per trajectory, with its
    token stream and — when ``agents`` (``aggregate["rl"]["agents"]``) names
    the same run — the return and the success the RL section computed."""
    returns: dict = {}
    for name, block in (agents or {}).items():
        for ep in (block or {}).get("episodes") or []:
            if isinstance(ep, dict):
                returns[(str(name), str(ep.get("task_id")), str(ep.get("run_id")))] = ep
    out: list = []
    for traj in trajectories or []:
        key = (str(traj.agent.name), str(traj.task.id), str(traj.run_id))
        known = returns.get(key) or {}
        ret = known.get("return")
        out.append({
            "policy": key[0], "task_id": key[1], "run_id": key[2],
            "tokens": episode_tokens(traj.steps),
            "return": float(ret) if isinstance(ret, (int, float)) and not isinstance(ret, bool) else None,
            "success": bool(known["success"]) if isinstance(known.get("success"), bool)
            else (traj.outcome.success is True),
        })
        out[-1]["steps"] = len(out[-1]["tokens"])
        out[-1]["key"] = "|".join(key)
    out.sort(key=lambda e: (e["policy"], e["task_id"], e["run_id"]))
    return out


def _clean(episodes: list, names: Optional[tuple]) -> tuple:
    """(episodes in canonical order, policy names in order)."""
    eps: list = []
    for i, raw in enumerate(episodes or []):
        if not isinstance(raw, dict):
            continue
        tokens = [str(t) for t in (raw.get("tokens") or [])]
        ret = raw.get("return")
        eps.append({
            "policy": str(raw.get("policy") or ""), "task_id": str(raw.get("task_id") or ""),
            "run_id": str(raw.get("run_id") or ""), "tokens": tokens,
            "return": float(ret) if isinstance(ret, (int, float)) and not isinstance(ret, bool) else None,
            "success": raw.get("success") is True, "steps": len(tokens),
        })
    eps.sort(key=lambda e: (e["policy"], e["task_id"], e["run_id"]))
    for e in eps:
        e["key"] = f"{e['policy']}|{e['task_id']}|{e['run_id']}"
    order = [str(n) for n in (names or [])]
    for e in eps:
        if e["policy"] not in order:
            order.append(e["policy"])
    return eps, order


# ---------------------------------------------------------------- vocabulary

def vocabulary(episodes: list, policies: list) -> dict:
    """Every token, its count and share per policy, and the tokens only one
    policy ever uses."""
    counts: dict = {p: {} for p in policies}
    totals: dict = {p: 0 for p in policies}
    for e in episodes:
        per = counts.setdefault(e["policy"], {})
        for tok in e["tokens"]:
            per[tok] = per.get(tok, 0) + 1
            totals[e["policy"]] = totals.get(e["policy"], 0) + 1
    tokens = sorted({t for per in counts.values() for t in per})
    overall = {t: sum(counts[p].get(t, 0) for p in counts) for t in tokens}
    signature: dict = {}
    for p in policies:
        signature[p] = sorted(t for t in tokens
                              if counts.get(p, {}).get(t, 0) > 0
                              and all(counts.get(q, {}).get(t, 0) == 0 for q in policies if q != p))
    rates = {p: {t: round(counts.get(p, {}).get(t, 0) / totals[p], 4) for t in tokens}
             for p in policies if totals.get(p)}
    return {
        "tokens": tokens, "size": len(tokens),
        "counts": overall,
        "frequency": {p: {t: counts.get(p, {}).get(t, 0) for t in tokens} for p in policies},
        "rates": {p: rates.get(p, {}) for p in policies},
        "steps": {p: totals.get(p, 0) for p in policies},
        "signature": {p: signature.get(p, []) for p in policies},
        "shared": sorted(t for t in tokens if all(counts.get(p, {}).get(t, 0) > 0 for p in policies)),
    }


# ---------------------------------------------------------------- the trie

def _node_stats(node: dict, eps: list, policies: list) -> None:
    node["episodes"] = len(eps)
    node["by_policy"] = {p: sum(1 for e in eps if e["policy"] == p) for p in policies}
    node["mean_return"] = _mean([e["return"] for e in eps])
    node["return_by_policy"] = {p: _mean([e["return"] for e in eps if e["policy"] == p]) for p in policies}
    ok = sum(1 for e in eps if e["success"])
    node["success_rate"] = round(ok / len(eps), 4) if eps else None
    node["successes"] = ok


def build_trie(episodes: list, policies: list, max_depth: int = MAX_DEPTH,
               min_tail: int = MIN_TAIL_EPISODES) -> dict:
    """The prefix tree over the token streams, pruned two ways and saying so:
    a subtree reached by under ``min_tail`` episodes is one leaf carrying the
    tokens folded under it, and nothing is built below ``max_depth``."""
    counter = {"n": 0}
    pruned = {"tails": 0, "tail_episodes": 0, "tokens_folded": 0, "truncated": 0, "truncated_tokens": 0}

    def build(eps: list, depth: int, prefix: list, token: Optional[str]) -> dict:
        node = {"id": f"n{counter['n']}", "depth": depth, "token": token, "prefix": list(prefix),
                "ends_here": sum(1 for e in eps if len(e["tokens"]) == depth),
                "tail": None, "truncated": None, "children": []}
        counter["n"] += 1
        _node_stats(node, eps, policies)
        going = [e for e in eps if len(e["tokens"]) > depth]
        if not going:
            return node
        if depth >= max_depth:
            node["truncated"] = max(len(e["tokens"]) for e in going) - depth
            pruned["truncated"] += 1
            pruned["truncated_tokens"] += sum(len(e["tokens"]) - depth for e in going)
            return node
        groups: dict = {}
        for e in going:
            groups.setdefault(e["tokens"][depth], []).append(e)
        for tok in sorted(groups, key=lambda t: (-len(groups[t]), t)):
            group = groups[tok]
            child = build(group, depth + 1, prefix + [tok], tok) if len(group) >= min_tail \
                else _tail(group, depth + 1, prefix + [tok], tok)
            node["children"].append(child)
        return node

    def _tail(eps: list, depth: int, prefix: list, token: str) -> dict:
        node = {"id": f"n{counter['n']}", "depth": depth, "token": token, "prefix": list(prefix),
                "ends_here": sum(1 for e in eps if len(e["tokens"]) == depth),
                "tail": max(len(e["tokens"]) for e in eps) - depth, "truncated": None, "children": []}
        counter["n"] += 1
        _node_stats(node, eps, policies)
        pruned["tails"] += 1
        pruned["tail_episodes"] += len(eps)
        pruned["tokens_folded"] += sum(len(e["tokens"]) - depth for e in eps)
        return node

    root = build(list(episodes), 0, [], None)
    deepest = max((len(e["tokens"]) for e in episodes), default=0)
    note = (f"{_plural(pruned['tails'], 'single-episode tail')} folded into a leaf "
            f"({pruned['tokens_folded']} tokens hidden)")
    if pruned["truncated"]:
        note += (f"; {_plural(pruned['truncated'], 'node')} cut at the depth cap of {max_depth} "
                 f"({pruned['truncated_tokens']} tokens hidden)")
    if not pruned["tails"] and not pruned["truncated"]:
        note = "nothing pruned: every branch carries at least "f"{min_tail} episodes and fits the depth cap"
    return {"root": root, "nodes": counter["n"], "max_depth": max_depth, "min_tail_episodes": min_tail,
            "deepest_stream": deepest,
            "per_policy": "every node carries by_policy, so one policy's own trie is this tree "
                          "restricted to the nodes it reaches (deepcompare.rlspace.policy_trie)",
            "pruned": dict(pruned, note=note)}


def policy_trie(root: dict, policy: str) -> Optional[dict]:
    """One policy's own prefix tree, read off the merged one: the nodes that
    policy's episodes actually pass through, with its own counts. The merged
    tree carries every policy's traffic at every node, so there is no second
    tree to build — this is that tree restricted, and it is what "per policy
    and merged" means here."""
    if not root or not root["by_policy"].get(policy):
        return None
    node = {k: v for k, v in root.items() if k != "children"}
    node["episodes"] = root["by_policy"].get(policy, 0)
    node["by_policy"] = {policy: node["episodes"]}
    node["children"] = [c for c in (policy_trie(k, policy) for k in root.get("children") or []) if c]
    return node


def _walk(node: dict):
    yield node
    for child in node.get("children") or []:
        yield from _walk(child)


def branch_points(trie: dict, episodes: list, policies: list, top: int = TOP_BRANCHES) -> dict:
    """Where the two policies part: every node with more than one child,
    scored by how unevenly the policies' traffic splits over those children
    (total variation between the two distributions) weighted by the episodes
    that reach it, with the child each policy leans to and the mean return
    below each."""
    pair = [p for p in policies if any(e["policy"] == p for e in episodes)][:2]
    if len(pair) < 2:
        return {"measurable": False, "reason": "a branch point needs two policies with episodes; "
                f"this batch has {_plural(len(pair), 'policy', 'policies')}", "points": [], "policies": pair}
    total = len(episodes) or 1
    a, b = pair
    points: list = []
    for node in _walk(trie["root"]):
        kids = node.get("children") or []
        if len(kids) < 2:
            continue
        na, nb = node["by_policy"].get(a, 0), node["by_policy"].get(b, 0)
        if not na or not nb:
            continue
        shares = []
        tv = 0.0
        for kid in kids:
            sa = kid["by_policy"].get(a, 0) / na
            sb = kid["by_policy"].get(b, 0) / nb
            tv += abs(sa - sb)
            shares.append((kid, sa, sb))
        tv = round(min(1.0, tv / 2.0), 4)
        weight = round(node["episodes"] / total, 4)
        children = [{"token": kid["token"], "id": kid["id"], "episodes": kid["episodes"],
                     "by_policy": dict(kid["by_policy"]), "share": {a: round(sa, 4), b: round(sb, 4)},
                     "mean_return": kid["mean_return"], "success_rate": kid["success_rate"],
                     "return_by_policy": dict(kid["return_by_policy"])}
                    for kid, sa, sb in shares]
        lean_a = max(shares, key=lambda s: (s[1] - s[2], -kids.index(s[0])))[0]
        lean_b = max(shares, key=lambda s: (s[2] - s[1], -kids.index(s[0])))[0]
        sides = [{"policy": a, "token": lean_a["token"], "id": lean_a["id"], "episodes": lean_a["episodes"],
                  "by_policy": dict(lean_a["by_policy"]), "mean_return": lean_a["mean_return"],
                  "success_rate": lean_a["success_rate"]},
                 {"policy": b, "token": lean_b["token"], "id": lean_b["id"], "episodes": lean_b["episodes"],
                  "by_policy": dict(lean_b["by_policy"]), "mean_return": lean_b["mean_return"],
                  "success_rate": lean_b["success_rate"]}]
        points.append({
            "id": node["id"], "depth": node["depth"], "prefix": list(node["prefix"]),
            "episodes": node["episodes"], "by_policy": dict(node["by_policy"]),
            "mean_return": node["mean_return"], "success_rate": node["success_rate"],
            "imbalance": tv, "weight": weight, "score": round(tv * weight, 4),
            "children": children, "sides": sides,
            "label": _branch_label(node, sides, tv),
        })
    points.sort(key=lambda p: (-p["score"], p["depth"], p["id"]))
    return {"measurable": bool(points), "reason": None if points else
            "no node has two children both policies reach, so the policies never measurably part",
            "policies": pair, "points": points[:top], "considered": len(points)}


def _branch_label(node: dict, sides: list, tv: float) -> str:
    head = f"step {node['depth']}"
    a, b = sides
    if a["token"] == b["token"]:
        return f"{head}: both policies take {a['token']} (imbalance {tv:.2f})"
    return (f"{head}: {a['policy']} takes {a['token']} (return {_num(a['mean_return'])}), "
            f"{b['policy']} takes {b['token']} (return {_num(b['mean_return'])})")


# ---------------------------------------------------------------- n-grams

def _grams(tokens: list, n: int) -> list:
    return [tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


def _count_grams(eps: list, n: int) -> tuple:
    counts: dict = {}
    episodes: dict = {}
    total = 0
    for e in eps:
        seen = set()
        for g in _grams(e["tokens"], n):
            counts[g] = counts.get(g, 0) + 1
            total += 1
            seen.add(g)
        for g in seen:
            episodes[g] = episodes.get(g, 0) + 1
    return counts, episodes, total


def _gram_text(gram: tuple) -> str:
    return " → ".join(gram)


def ngrams(episodes: list, policies: list, sizes: tuple = NGRAM_SIZES, top: int = TOP_NGRAMS,
           min_count: int = MIN_NGRAM_COUNT) -> dict:
    """The commonest grams per policy, and the ones over- and under-
    represented in the episodes that succeeded against the ones that failed
    — a ratio of rates, every count carried."""
    by_policy: dict = {}
    for p in policies:
        eps = [e for e in episodes if e["policy"] == p]
        per: dict = {}
        for n in sizes:
            counts, in_eps, total = _count_grams(eps, n)
            rows = sorted(counts, key=lambda g: (-counts[g], _gram_text(g)))[:top]
            per[str(n)] = [{"n": n, "gram": list(g), "text": _gram_text(g), "count": counts[g],
                            "episodes": in_eps.get(g, 0),
                            "rate": round(counts[g] / total, 4) if total else None} for g in rows]
        by_policy[p] = per
    winners = [e for e in episodes if e["success"]]
    losers = [e for e in episodes if not e["success"]]
    note = ("a gram's rate is its share of every gram of that length in the group, so a shorter "
            "winning episode raises the rate of everything it does; per_episode says the same thing "
            "per episode instead, and both counts are here to be read against the ratio")
    if not winners or not losers:
        why = ("every episode succeeded" if not losers else "no episode succeeded") if episodes \
            else "there are no episodes"
        winning = {"measurable": False, "reason": f"the winning/losing ratio needs both: {why}",
                   "winners": len(winners), "losers": len(losers), "min_count": min_count,
                   "top": [], "bottom": [], "note": note}
        return {"sizes": list(sizes), "top_n": top, "by_policy": by_policy, "winning": winning}
    rows: list = []
    grams_win, grams_lose = {}, {}
    for n in sizes:
        wc, we, wt = _count_grams(winners, n)
        lc, le, lt = _count_grams(losers, n)
        grams_win[str(n)], grams_lose[str(n)] = wt, lt
        for g in sorted(set(wc) | set(lc), key=_gram_text):
            if max(wc.get(g, 0), lc.get(g, 0)) < min_count:
                continue
            wr = wc.get(g, 0) / wt if wt else 0.0
            lr = lc.get(g, 0) / lt if lt else 0.0
            ratio = round(wr / lr, 4) if lr > 0 else None
            rows.append({"n": n, "gram": list(g), "text": _gram_text(g), "ratio": ratio,
                         "win_count": wc.get(g, 0), "lose_count": lc.get(g, 0),
                         "win_rate": round(wr, 6), "lose_rate": round(lr, 6),
                         "win_per_episode": round(wc.get(g, 0) / len(winners), 4),
                         "lose_per_episode": round(lc.get(g, 0) / len(losers), 4),
                         "win_episodes": we.get(g, 0), "lose_episodes": le.get(g, 0),
                         "only_in_winners": lc.get(g, 0) == 0, "only_in_losers": wc.get(g, 0) == 0})
    # over-represented in the winners first (a gram the losers never played has
    # no ratio at all and leads); the other end is the losing habit
    over = [r for r in rows if r["win_count"] >= min_count]
    over.sort(key=lambda r: (0 if r["ratio"] is None else 1,
                             -(r["ratio"] or 0.0), -r["win_count"], r["n"], r["text"]))
    under = [r for r in rows if r["lose_count"] >= min_count]
    under.sort(key=lambda r: (0 if r["ratio"] is None else 1,
                              (r["ratio"] if r["ratio"] is not None else 0.0), -r["lose_count"],
                              r["n"], r["text"]))
    winning = {"measurable": bool(over), "reason": None if over else
               f"no gram reaches {min_count} occurrences among the winning episodes",
               "winners": len(winners), "losers": len(losers), "min_count": min_count,
               "win_grams": grams_win, "lose_grams": grams_lose,
               "win_steps_mean": _mean([len(e["tokens"]) for e in winners]),
               "lose_steps_mean": _mean([len(e["tokens"]) for e in losers]),
               "top": over[:top], "bottom": under[:top], "note": note}
    return {"sizes": list(sizes), "top_n": top, "by_policy": by_policy, "winning": winning}


# ---------------------------------------------------------------- distance

def _edit_dp(a: list, b: list) -> int:
    """Levenshtein by the textbook row DP — the reference the fast path is
    checked against."""
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def _edit_myers(a: list, b: list) -> int:
    """Levenshtein by Myers' bit-parallel algorithm: the DP's columns held as
    two bit vectors (one Python int each), so a column costs a handful of
    integer operations instead of one per row. Exact — the same number the
    row DP gives — and the reason a batch of long episodes is not quadratic
    in tokens twice over."""
    m = len(a)
    full = (1 << m) - 1
    peq: dict = {}
    for i, tok in enumerate(a):
        peq[tok] = peq.get(tok, 0) | (1 << i)
    vp, vn, score, top = full, 0, m, 1 << (m - 1)
    for tok in b:
        eq = peq.get(tok, 0)
        xv = eq | vn
        xh = (((eq & vp) + vp) ^ vp) | eq
        ph = vn | ~(xh | vp)
        mh = vp & xh
        if ph & top:
            score += 1
        elif mh & top:
            score -= 1
        ph = ((ph << 1) | 1) & full
        mh = (mh << 1) & full
        vp = (mh | ~(xv | ph)) & full
        vn = ph & xv
    return score


def edit_distance(a: list, b: list) -> int:
    """Levenshtein distance between two token streams. Equal shared prefixes
    and suffixes are trimmed first (which changes nothing and saves the
    work), then Myers' bit-parallel algorithm does the rest."""
    if a == b:
        return 0
    n, m = len(a), len(b)
    if not n:
        return m
    if not m:
        return n
    start = 0
    while start < n and start < m and a[start] == b[start]:
        start += 1
    end = 0
    while end < n - start and end < m - start and a[n - 1 - end] == b[m - 1 - end]:
        end += 1
    a2, b2 = a[start:n - end], b[start:m - end]
    if not a2:
        return len(b2)
    if not b2:
        return len(a2)
    # the pattern is the longer stream: the columns are bits (a few machine
    # words either way), the iterations are the other stream's length
    return _edit_myers(a2, b2) if len(a2) >= len(b2) else _edit_myers(b2, a2)


def normalised_distance(a: list, b: list) -> float:
    """Edit distance over the longer stream's length — 0 (the same stream)
    to 1 (nothing in common)."""
    longest = max(len(a), len(b))
    return 0.0 if not longest else round(edit_distance(a, b) / longest, 4)


def _key(e: dict) -> str:
    return e.get("key") or f"{e.get('policy')}|{e.get('task_id')}|{e.get('run_id')}"


def _select(episodes: list, policies: list, cap: int) -> list:
    """At most ``cap`` episodes, round-robin over the policies in canonical
    order so both are represented, then back in canonical order."""
    if len(episodes) <= cap:
        return list(episodes)
    queues = {p: [e for e in episodes if e["policy"] == p] for p in policies}
    picked: list = []
    i = 0
    while len(picked) < cap and any(len(queues[p]) > i for p in policies):
        for p in policies:
            if len(picked) >= cap:
                break
            if len(queues[p]) > i:
                picked.append(queues[p][i])
        i += 1
    picked.sort(key=lambda e: (e["policy"], e["task_id"], e["run_id"]))
    return picked


def distances(episodes: list, policies: list, cap: int = MAX_DISTANCE_EPISODES,
              token_cap: int = MAX_DISTANCE_TOKENS) -> dict:
    """The pairwise normalised edit distance matrix over (at most ``cap``)
    episodes, each policy's behavioural spread, the distance between the
    policies, and every episode's nearest neighbour."""
    chosen = _select(episodes, policies, cap)
    streams = [e["tokens"][:token_cap] for e in chosen]
    truncated = sum(1 for e in chosen if len(e["tokens"]) > token_cap)
    n = len(chosen)
    matrix = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            d = normalised_distance(streams[i], streams[j])
            matrix[i][j] = matrix[j][i] = d
    within: dict = {}
    for p in policies:
        idx = [i for i, e in enumerate(chosen) if e["policy"] == p]
        pairs = [matrix[i][j] for a, i in enumerate(idx) for j in idx[a + 1:]]
        within[p] = {"episodes": len(idx), "spread": round(sum(pairs) / len(pairs), 4) if pairs else None,
                     "pairs": len(pairs),
                     "reason": None if pairs else f"{_plural(len(idx), 'episode')}: a spread needs two"}
    between = None
    cross_pairs = 0
    if len(policies) >= 2:
        a, b = policies[0], policies[1]
        ia = [i for i, e in enumerate(chosen) if e["policy"] == a]
        ib = [i for i, e in enumerate(chosen) if e["policy"] == b]
        vals = [matrix[i][j] for i in ia for j in ib]
        cross_pairs = len(vals)
        between = round(sum(vals) / len(vals), 4) if vals else None
    nearest: list = []
    for i in range(n):
        others = [(matrix[i][j], _key(chosen[j]), j) for j in range(n) if j != i]
        if not others:
            nearest.append(None)
            continue
        d, key, j = min(others, key=lambda t: (t[0], t[1]))
        cross = [t for t in others if chosen[t[2]]["policy"] != chosen[i]["policy"]]
        other = None
        if cross:
            od, okey, oj = min(cross, key=lambda t: (t[0], t[1]))
            other = {"key": okey, "policy": chosen[oj]["policy"], "distance": od}
        nearest.append({"key": key, "policy": chosen[j]["policy"], "distance": d,
                        "other_policy": chosen[j]["policy"] != chosen[i]["policy"],
                        "nearest_other": other})
    return {
        "metric": "normalised edit distance (Levenshtein over the token streams, divided by the longer one)",
        "episodes": [{"key": _key(e), "policy": e["policy"], "task_id": e["task_id"], "run_id": e["run_id"],
                      "return": e.get("return"), "success": e.get("success") is True,
                      "steps": len(e["tokens"])} for e in chosen],
        "matrix": matrix, "matrix_note": None, "within": within, "between": between, "cross_pairs": cross_pairs,
        "counted": n, "of": len(episodes), "capped": n < len(episodes),
        "episode_cap": cap, "token_cap": token_cap, "tokens_truncated": truncated,
        "nearest": nearest,
        "note": (f"{n} of {len(episodes)} episodes, round-robin over the policies" if n < len(episodes)
                 else f"every one of {n} episodes")
        + (f"; {_plural(truncated, 'stream')} cut at {token_cap} tokens" if truncated else ""),
    }


# ---------------------------------------------------------------- MDS

def _lcg(seed: int, n: int) -> list:
    """A fixed pseudo-random start vector — an LCG written out, so the
    layout cannot drift with the standard library's own generator."""
    out: list = []
    x = seed % 2147483647 or 1
    for _ in range(n):
        x = (1103515245 * x + 12345) % 2147483648
        out.append(x / 2147483648.0 * 2.0 - 1.0)
    return out


def _power(matrix: list, seed: int, iterations: int) -> tuple:
    """(eigenvalue, eigenvector) of the largest-magnitude eigenvalue by power
    iteration from a fixed vector for a fixed number of iterations; the sign
    is fixed by the largest-magnitude entry so the result is reproducible."""
    n = len(matrix)
    if not n:
        return 0.0, []
    v = _lcg(seed, n)
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    v = [x / norm for x in v]
    value = 0.0
    for _ in range(iterations):
        w = [sum(matrix[i][j] * v[j] for j in range(n)) for i in range(n)]
        norm = math.sqrt(sum(x * x for x in w))
        if norm < 1e-12:
            return 0.0, [0.0] * n
        v = [x / norm for x in w]
        value = norm
    # the Rayleigh quotient keeps the sign a negative eigenvalue would lose
    w = [sum(matrix[i][j] * v[j] for j in range(n)) for i in range(n)]
    value = sum(v[i] * w[i] for i in range(n))
    lead = max(range(n), key=lambda i: (abs(v[i]), -i))
    if v[lead] < 0:
        v = [-x for x in v]
    return value, v


def mds(matrix: list, iterations: int = POWER_ITERS, seed: int = MDS_SEED) -> dict:
    """Classical multidimensional scaling in pure Python: double-centre the
    squared distances, take the top two eigenvectors by power iteration with
    deflation. **The axes carry no meaning** — no unit, no direction; only
    the relative position of two marks says anything, and only as well as a
    plane can hold the distances (``stress`` says how well)."""
    n = len(matrix)
    if n == 0:
        return {"points": [], "eigenvalues": [], "stress": None, "iterations": iterations, "seed": seed}
    if n == 1:
        return {"points": [[0.0, 0.0]], "eigenvalues": [0.0, 0.0], "stress": 0.0,
                "iterations": iterations, "seed": seed}
    sq = [[matrix[i][j] * matrix[i][j] for j in range(n)] for i in range(n)]
    rows = [sum(r) / n for r in sq]
    grand = sum(rows) / n
    b = [[-0.5 * (sq[i][j] - rows[i] - rows[j] + grand) for j in range(n)] for i in range(n)]
    l1, v1 = _power(b, seed, iterations)
    if l1 > 0:
        b2 = [[b[i][j] - l1 * v1[i] * v1[j] for j in range(n)] for i in range(n)]
    else:
        b2 = b
    l2, v2 = _power(b2, seed + 1, iterations)
    s1 = math.sqrt(l1) if l1 > 0 else 0.0
    s2 = math.sqrt(l2) if l2 > 0 else 0.0
    points = [[round(s1 * v1[i], 6), round(s2 * (v2[i] if v2 else 0.0), 6)] for i in range(n)]
    num = den = 0.0
    for i in range(n):
        for j in range(i + 1, n):
            d = math.hypot(points[i][0] - points[j][0], points[i][1] - points[j][1])
            num += (d - matrix[i][j]) ** 2
            den += matrix[i][j] ** 2
    stress = round(math.sqrt(num / den), 4) if den > 0 else 0.0
    return {"points": points, "eigenvalues": [round(l1, 6), round(l2, 6)], "stress": stress,
            "iterations": iterations, "seed": seed}


# ---------------------------------------------------------------- public

def behaviour_space(episodes: list, names: Optional[tuple] = None, max_depth: int = MAX_DEPTH,
                    min_tail: int = MIN_TAIL_EPISODES, distance_cap: int = MAX_DISTANCE_EPISODES,
                    token_cap: int = MAX_DISTANCE_TOKENS) -> dict:
    """``aggregate["rl"]["space"]`` from behaviour episodes (``{policy,
    task_id, run_id, tokens, return, success}``): the action vocabulary, the
    policy trie with its branch points, the n-gram habits, the distance
    matrix with each policy's spread, and a deterministic 2-D layout."""
    eps, policies = _clean(episodes, names)
    vocab = vocabulary(eps, policies)
    empty = {"version": VERSION, "measurable": False, "policies": policies,
             "episodes_n": len(eps), "vocabulary": vocab, "trie": None, "branches": None,
             "ngrams": None, "distance": None, "layout": None}
    if not eps:
        return dict(empty, reason="no episode to read", narrative="No episode to read as behaviour.")
    if not vocab["size"]:
        return dict(empty, reason="no episode carries a step, so there is no behaviour to compare",
                    narrative=f"{_plural(len(eps), 'episode')}, none with a step: no behaviour to compare.")
    trie = build_trie(eps, policies, max_depth=max_depth, min_tail=min_tail)
    branches = branch_points(trie, eps, policies)
    grams = ngrams(eps, policies)
    dist = distances(eps, policies, cap=distance_cap, token_cap=token_cap)
    layout = mds(dist["matrix"])
    # the matrix is what everything above was read off; carrying N² numbers
    # into the page on top of that is weight, not honesty
    if dist["counted"] > MATRIX_JSON_EPISODES:
        n = dist["counted"]
        dist = dict(dist, matrix=None, matrix_note=(
            f"the {n}×{n} matrix is not carried ({n * n} numbers); the spreads, the distance between "
            f"the policies, every nearest neighbour and the layout are all read off it"))
    # the layout's marks carry their own token stream: it is what the page
    # needs to highlight the episodes an n-gram habit actually appears in
    streams = {e["key"]: e["tokens"] for e in eps}
    points: list = []
    for i, e in enumerate(dist["episodes"]):
        xy = layout["points"][i] if i < len(layout["points"]) else [0.0, 0.0]
        near = dist["nearest"][i] if i < len(dist["nearest"]) else None
        points.append(dict(e, x=xy[0], y=xy[1], nearest=near, tokens=streams.get(e["key"], [])))
    space = {
        "version": VERSION, "measurable": True, "reason": None, "policies": policies,
        "episodes_n": len(eps), "vocabulary": vocab, "trie": trie, "branches": branches,
        "ngrams": grams, "distance": dist,
        "layout": {"points": points, "eigenvalues": layout["eigenvalues"], "stress": layout["stress"],
                   "iterations": layout["iterations"], "seed": layout["seed"],
                   "axes": "none: classical MDS of the distance matrix, so only relative position means anything"},
    }
    space["narrative"] = _narrative(space)
    return space


def _narrative(space: dict) -> str:
    vocab, parts = space["vocabulary"], []
    parts.append(f"{_plural(space['episodes_n'], 'episode')} over {_plural(vocab['size'], 'token')} "
                 f"({', '.join(vocab['tokens'][:6])}{'…' if vocab['size'] > 6 else ''})")
    sigs = [f"{p} alone uses {', '.join(t)}" for p, t in sorted(vocab["signature"].items()) if t]
    parts.append("; ".join(sigs) if sigs else "no token belongs to one policy alone")
    br = space["branches"]
    if br and br.get("points"):
        top = br["points"][0]
        parts.append("they part most at " + top["label"].lower())
    elif br:
        parts.append(br.get("reason") or "no branch point")
    spread = space["distance"]["within"]
    said = [f"{p}'s episodes sit {_num(v['spread'])} apart" for p, v in sorted(spread.items())
            if v.get("spread") is not None]
    if said:
        parts.append("; ".join(said)
                     + (f", the policies {_num(space['distance']['between'])} apart"
                        if space["distance"]["between"] is not None else ""))
    win = space["ngrams"]["winning"]
    if win.get("measurable") and win["top"]:
        t = win["top"][0]
        said = (f"the strongest winning habit is {t['text']} "
                f"({t['win_count']} against {t['lose_count']}"
                + (f", {t['ratio']:.2f}×)" if t["ratio"] is not None else ", never in a losing episode)"))
        if win.get("bottom"):
            b = win["bottom"][0]
            said += (f", the strongest losing one {b['text']} ({b['lose_count']} against {b['win_count']}"
                     + (f", {b['ratio']:.2f}×)" if b["ratio"] is not None else ")"))
        parts.append(said)
    return "; ".join(parts) + "."


def rl_space(trajectories: list, agents: Optional[dict] = None, names: Optional[tuple] = None,
             **kwargs) -> dict:
    """``aggregate["rl"]["space"]`` straight from the batch's trajectories and
    the RL section's per-agent episodes."""
    return behaviour_space(behaviour_episodes(trajectories, agents), names=names, **kwargs)


__all__ = ["behaviour_space", "behaviour_episodes", "rl_space", "episode_tokens", "step_token",
           "vocabulary", "build_trie", "policy_trie", "branch_points", "ngrams", "distances", "edit_distance",
           "normalised_distance", "mds", "VERSION", "MAX_DEPTH", "MIN_TAIL_EPISODES",
           "MAX_DISTANCE_EPISODES", "MAX_DISTANCE_TOKENS", "NGRAM_SIZES", "TOP_NGRAMS",
           "MIN_NGRAM_COUNT", "TOP_BRANCHES", "POWER_ITERS", "MDS_SEED"]
