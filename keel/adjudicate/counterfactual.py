"""Pearl's three steps, executed: abduction -> action -> prediction.

PN = P(Y_{do(X=0)} = 0 | X=1, Y=1): abduct the exogenous noise from the
observed incident, sever X, propagate, count the worlds where the outage
vanishes. PS = P(Y_{do(X=1)} = 1 | X=0, Y=0): rejection-sample worlds where
neither X nor the outage occurred, force X on, count the worlds that break.

Identifiability: monotonicity holds by construction in a noisy-OR network
(causes never prevent effects), which point-identifies PN given the model.
The honest failure mode is *model* misspecification via latent confounding —
detected structurally (shared unobserved feeds) — in which case we report
Tian-Pearl bounds from observational data instead.

Confidence intervals: percentile bootstrap over the posterior sample set.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from ..config import ABDUCTION_SAMPLES, BOOTSTRAP_ROUNDS
from .scm import OUTCOME, InstanceSCM


def _bootstrap_ci(hits: np.ndarray, alpha: float = 0.10,
                  rounds: int = BOOTSTRAP_ROUNDS,
                  rng: Optional[np.random.Generator] = None
                  ) -> tuple[float, float]:
    rng = rng or np.random.default_rng(0)
    n = len(hits)
    if n == 0:
        return 0.0, 1.0
    means = [float(hits[rng.integers(0, n, n)].mean()) for _ in range(rounds)]
    return (round(float(np.percentile(means, 5)), 4),
            round(float(np.percentile(means, 95)), 4))


def probability_of_necessity(scm: InstanceSCM, evidence: dict[str, int],
                             x: str, n: int = ABDUCTION_SAMPLES,
                             seed: int = 11) -> tuple[float, float, float]:
    """Requires factual X=1, Y=1 (guaranteed by the pipeline)."""
    rng = np.random.default_rng(seed)
    U = scm.abduct(evidence, n, rng)
    cf = scm.forward(U, {x: 0}, n)
    hits = (cf[OUTCOME] == 0).astype(np.float64)
    pn = float(hits.mean())
    lo, hi = _bootstrap_ci(hits, rng=rng)
    return round(pn, 4), lo, hi


def probability_of_sufficiency(scm: InstanceSCM, x: str,
                               n: int = ABDUCTION_SAMPLES, seed: int = 13,
                               max_batches: int = 40
                               ) -> tuple[float, float, float]:
    """Rejection sampling of (X=0, Y=0) worlds from the model prior."""
    rng = np.random.default_rng(seed)
    kept: list[np.ndarray] = []
    total = 0
    for _ in range(max_batches):
        U = scm.abduct({}, n, rng)                    # unconditioned prior draw
        factual = U["factual"]
        mask = (factual.get(x, np.zeros(n, dtype=np.int8)) == 0) & \
               (factual[OUTCOME] == 0)
        if mask.any():
            cf = scm.forward(U, {x: 1}, n)
            kept.append((cf[OUTCOME] == 1).astype(np.float64)[mask])
            total += int(mask.sum())
        if total >= n:
            break
    if not kept:
        return 0.0, 0.0, 1.0
    hits = np.concatenate(kept)
    ps = float(hits.mean())
    lo, hi = _bootstrap_ci(hits, rng=rng)
    return round(ps, 4), lo, hi
