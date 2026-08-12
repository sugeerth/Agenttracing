"""Joint attribute effects by deterministic logistic regression (v16).

:mod:`deepcompare.attributes` scores each attribute on its own.  That leaves
a question it cannot answer: when several attributes look predictive, are
they several signals or one signal counted several times?  Runs with a
weak step also tend to be the runs that skipped verification, and a marginal
table will credit both.

A logistic model fits all attributes at once, so each coefficient is read
"holding the others fixed".  The implementation is iteratively reweighted
least squares with a ridge penalty, written out in plain Python:

* **Deterministic.**  A fixed iteration cap and a fixed tolerance, no random
  initialisation, no sampling — the same corpus always produces the same
  coefficients, which a gate decision can depend on.
* **Ridge-penalised.**  Eval corpora routinely contain a perfectly separating
  attribute (every run with a bad step failed).  Unpenalised maximum
  likelihood diverges there — coefficients run off to infinity and the fit
  silently becomes meaningless.  The penalty keeps the solution finite, and
  separation is detected and reported rather than hidden.
* **No dependencies.**  The normal equations are solved by Gaussian
  elimination with partial pivoting; at seven or eight attributes the system
  is tiny.

The output is still associational.  Controlling for the other *measured*
attributes is not controlling for everything, and with the sample sizes eval
suites have, coefficients are indicative rather than precise — which is why
they are reported alongside the marginal lifts rather than replacing them.
"""

from __future__ import annotations

import math
from typing import Optional

from .attributes import ATTRIBUTES
from .trace import Trajectory

#: ridge penalty on the coefficients (not the intercept).
RIDGE = 1.0
#: IRLS iteration cap — fixed, so the fit is reproducible.
MAX_ITER = 50
#: convergence tolerance on the maximum coefficient change.
TOLERANCE = 1e-8
#: minimum runs per fitted parameter before the fit is called reliable.
RUNS_PER_PARAMETER = 5


def _solve(matrix: list[list[float]], vector: list[float]) -> Optional[list[float]]:
    """Solve a small dense linear system by Gaussian elimination.

    Returns None when the system is singular to working precision.
    """
    n = len(vector)
    augmented = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for column in range(n):
        pivot = max(range(column, n), key=lambda r: abs(augmented[r][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            return None
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        pivot_value = augmented[column][column]
        for row in range(column + 1, n):
            factor = augmented[row][column] / pivot_value
            if factor == 0.0:
                continue
            for k in range(column, n + 1):
                augmented[row][k] -= factor * augmented[column][k]
    solution = [0.0] * n
    for row in range(n - 1, -1, -1):
        total = augmented[row][n] - sum(
            augmented[row][k] * solution[k] for k in range(row + 1, n)
        )
        solution[row] = total / augmented[row][row]
    return solution


def _sigmoid(z: float) -> float:
    # Split by sign to avoid overflow on large-magnitude linear predictors.
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    exp_z = math.exp(z)
    return exp_z / (1.0 + exp_z)


def _fit(design: list[list[float]], outcomes: list[int]) -> dict:
    """Ridge-penalised IRLS.  ``design`` includes the intercept column."""
    n = len(outcomes)
    p = len(design[0])
    beta = [0.0] * p
    iterations = 0
    converged = False

    for iterations in range(1, MAX_ITER + 1):
        # Working weights and response.
        gradient = [0.0] * p
        hessian = [[0.0] * p for _ in range(p)]
        for i in range(n):
            eta = sum(design[i][j] * beta[j] for j in range(p))
            mu = _sigmoid(eta)
            weight = max(mu * (1 - mu), 1e-9)
            residual = outcomes[i] - mu
            for j in range(p):
                gradient[j] += design[i][j] * residual
                for k in range(p):
                    hessian[j][k] += design[i][j] * weight * design[i][k]
        # Ridge on the slopes only; the intercept stays unpenalised so the
        # base rate is not shrunk toward a half.
        for j in range(1, p):
            hessian[j][j] += RIDGE
            gradient[j] -= RIDGE * beta[j]

        step = _solve(hessian, gradient)
        if step is None:
            return {"beta": beta, "iterations": iterations, "converged": False,
                    "singular": True}
        beta = [beta[j] + step[j] for j in range(p)]
        if max(abs(s) for s in step) < TOLERANCE:
            converged = True
            break

    return {"beta": beta, "iterations": iterations, "converged": converged,
            "singular": False}


def _separates(values: list[bool], outcomes: list[int]) -> bool:
    """True when an attribute perfectly predicts the outcome either way."""
    with_out = {outcomes[i] for i, v in enumerate(values) if v}
    without_out = {outcomes[i] for i, v in enumerate(values) if not v}
    if not with_out or not without_out:
        return False
    return len(with_out) == 1 and len(without_out) == 1 and with_out != without_out


def joint_attribute_model(trajectories: list[Trajectory]) -> dict:
    """Fit failure on all measurable attributes at once.

    Attributes that cannot be measured for every run (model confidence on a
    corpus where only some runs carry telemetry) are dropped rather than
    imputed, and named in ``dropped`` so the omission is visible.
    """
    if not trajectories:
        return {"available": False, "reason": "no runs", "coefficients": []}

    outcomes = [0 if t.outcome.success else 1 for t in trajectories]
    if len(set(outcomes)) < 2:
        return {
            "available": False,
            "reason": "every run has the same outcome; nothing to model",
            "coefficients": [],
        }

    columns: list[str] = []
    values: dict[str, list[bool]] = {}
    dropped: list[dict] = []
    for name, (predicate, _) in sorted(ATTRIBUTES.items()):
        measured = [predicate(t) for t in trajectories]
        if any(v is None for v in measured):
            dropped.append({"attribute": name, "reason": "not measurable for every run"})
            continue
        booleans = [bool(v) for v in measured]
        if all(booleans) or not any(booleans):
            dropped.append({"attribute": name, "reason": "constant across the corpus"})
            continue
        columns.append(name)
        values[name] = booleans

    if not columns:
        return {"available": False,
                "reason": "no attribute varies across the corpus",
                "coefficients": [], "dropped": dropped}

    design = [
        [1.0] + [1.0 if values[name][i] else 0.0 for name in columns]
        for i in range(len(trajectories))
    ]
    fit = _fit(design, outcomes)
    beta = fit["beta"]

    rows: list[dict] = []
    for index, name in enumerate(columns, start=1):
        coefficient = beta[index]
        rows.append({
            "attribute": name,
            "phrasing": ATTRIBUTES[name][1],
            "coefficient": round(coefficient, 4),
            "odds_ratio": round(math.exp(max(-50.0, min(50.0, coefficient))), 4),
            "separates": _separates(values[name], outcomes),
            "direction": ("raises" if coefficient > 0 else
                          "lowers" if coefficient < 0 else "does not move"),
        })
    rows.sort(key=lambda r: (-abs(r["coefficient"]), r["attribute"]))

    parameters = len(columns) + 1
    reliable = len(trajectories) >= RUNS_PER_PARAMETER * parameters
    separated = [r["attribute"] for r in rows if r["separates"]]

    top = rows[0] if rows else None
    narrative_parts: list[str] = []
    if top:
        narrative_parts.append(
            f"Holding the other {len(columns) - 1} attribute(s) fixed, "
            f"{top['phrasing']} {top['direction']} the odds of failure most "
            f"(odds ratio {top['odds_ratio']:g})."
        )
    if not reliable:
        narrative_parts.append(
            f"With {len(trajectories)} run(s) and {parameters} parameter(s) this "
            f"fit is indicative only — it wants about "
            f"{RUNS_PER_PARAMETER * parameters} runs to be read confidently."
        )
    if separated:
        narrative_parts.append(
            f"{', '.join(separated)} perfectly separate(s) the outcomes; the "
            f"ridge penalty keeps the coefficient finite, but its magnitude is "
            f"set by the penalty rather than by the data."
        )
    if not fit["converged"]:
        narrative_parts.append(
            f"The fit did not converge within {MAX_ITER} iterations; treat the "
            f"coefficients as unstable."
        )

    return {
        "available": True,
        "runs": len(trajectories),
        "failures": sum(outcomes),
        "parameters": parameters,
        "coefficients": rows,
        "intercept": round(beta[0], 4),
        "dropped": dropped,
        "ridge": RIDGE,
        "iterations": fit["iterations"],
        "converged": fit["converged"],
        "reliable": reliable,
        "method": (
            "ridge-penalised logistic regression (IRLS, fixed iteration cap, "
            "deterministic)"
        ),
        "caveat": (
            "Coefficients control for the other measured attributes only, and "
            "not for task difficulty. They remain associations."
        ),
        "narrative": " ".join(narrative_parts),
    }
