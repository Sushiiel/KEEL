"""Constrained contextual bandit for the ALLOW/ESCALATE decision (RL).

The gateway faces a sequential decision under a safety constraint: for each
(agent, action-class) it must choose ALLOW vs ESCALATE to maximize useful
autonomy (successful actions taken without a human) while keeping the realized
harm rate under a budget. This is a constrained multi-armed bandit.

We use **Thompson sampling** over a Beta-Bernoulli success model with a
**Lagrangian harm penalty** (the CMDP/Lagrangian-RL pattern): sample a success
rate θ ~ Beta(successes+1, failures+1), subtract λ·(harm posterior mean), and
recommend ALLOW only when the penalized sample clears the risk-class bar. λ is
adapted online toward the harm budget (dual gradient ascent), so the policy
tightens exactly when harm rises and relaxes when it's safe — learned, not
configured.

This is *advisory*: it composes with the anytime-valid lower bound (which
holds the hard guarantee) and can only make the gate MORE conservative, never
less. It gives the autonomy decision an exploration-aware, harm-constrained
policy instead of a fixed threshold.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from ..store import Store

_BANDIT_KEY = "gw_bandit"          # per-stratum {lam} dual variables


def _rng(seed_material: str) -> np.random.Generator:
    # deterministic per-call stream (Math.random is unavailable in workflows,
    # but here we just want reproducibility keyed by the stratum + counts)
    return np.random.default_rng(abs(hash(seed_material)) % (2**32))


def recommend(store: Store, stratum: str, successes: int, failures: int,
              harms: int, n: int, risk: str, harm_budget: float,
              floor_required: float, samples: int = 512) -> dict[str, Any]:
    """Thompson-sampling, harm-constrained recommendation for one stratum.

    Returns {allow: bool, p_allow: float, theta_mean, harm_mean, lam} — an
    advisory that the engine folds in (it may only tighten the decision)."""
    bandit = store.kv_get(_BANDIT_KEY, {})
    lam = float(bandit.get(stratum, {}).get("lam", 1.0))

    rng = _rng(f"{stratum}:{n}")
    theta = rng.beta(successes + 1, failures + 1, size=samples)     # success posterior
    harm = rng.beta(harms + 1, max(n - harms, 0) + 1, size=samples)  # harm posterior
    # Lagrangian-penalized utility of ALLOW: reward success, penalize harm.
    penalized = theta - lam * harm
    # recommend ALLOW when the penalized posterior clears the risk bar with
    # high posterior probability (exploration shrinks as n grows)
    clears = penalized >= floor_required
    p_allow = float(clears.mean())
    allow = p_allow >= 0.90

    # dual update: push λ toward satisfying E[harm] <= budget
    harm_mean = float(harm.mean())
    step = 0.5 / math.sqrt(n + 1)
    lam = max(0.0, min(50.0, lam + step * (harm_mean - harm_budget)))
    bandit[stratum] = {"lam": round(lam, 4)}
    store.kv_set(_BANDIT_KEY, bandit)

    return {"allow": allow, "p_allow": round(p_allow, 4),
            "theta_mean": round(float(theta.mean()), 4),
            "harm_mean": round(harm_mean, 4), "lam": round(lam, 4),
            "policy": "thompson+lagrangian"}
