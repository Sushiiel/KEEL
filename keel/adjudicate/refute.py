"""Mandatory refutation suite. A hypothesis that fails refutation is
downgraded regardless of its PN, and the results go on the certificate.

  placebo            swap the treatment for a random non-ancestor variable;
                     the PN should collapse. If the placebo scores high, the
                     model is crediting anything upstream-shaped.
  random_common_cause add a synthetic confounder feeding both X and Y;
                     the PN should barely move.
  evidence_subset    recompute on a random 60% of the evidence; the PN
                     should be stable if the conclusion rests on the cascade,
                     not on one fragile observation.
"""
from __future__ import annotations

import numpy as np

from ..models import RefutationResult
from .counterfactual import probability_of_necessity
from .scm import OUTCOME, InstanceSCM


def _ancestors(scm: InstanceSCM, node: str) -> set[str]:
    out: set[str] = set()
    frontier = [node]
    while frontier:
        v = frontier.pop()
        for p, _ in scm.parents.get(v, []):
            if p not in out:
                out.add(p)
                frontier.append(p)
    return out


def run_refuters(scm: InstanceSCM, evidence: dict[str, int], x: str,
                 pn: float, seed: int = 23, fast_n: int = 1200
                 ) -> list[RefutationResult]:
    rng = np.random.default_rng(seed)
    results: list[RefutationResult] = []

    # 1 ── placebo treatment
    outage_anc = _ancestors(scm, OUTCOME)
    candidates = [v for v in scm.nodes
                  if v != x and v != OUTCOME and v not in outage_anc
                  and evidence.get(v, 0) == 1]
    if not candidates:
        candidates = [v for v in scm.nodes
                      if v not in (x, OUTCOME) and evidence.get(v, 0) == 1
                      and v not in _ancestors(scm, x) | {x}]
    if candidates:
        placebo = str(rng.choice(sorted(candidates)))
        pn_placebo, _, _ = probability_of_necessity(
            scm, evidence, placebo, n=fast_n, seed=int(rng.integers(1e6)))
        passed = pn_placebo <= max(0.30, 0.5 * pn)
        results.append(RefutationResult(
            refuter="placebo_treatment", passed=passed,
            delta=round(pn_placebo, 4),
            detail=f"placebo {placebo} scored PN={pn_placebo:.2f} "
                   f"(claim PN={pn:.2f}) — {'collapses as expected' if passed else 'model credits placebos'}"))
    else:
        results.append(RefutationResult(
            refuter="placebo_treatment", passed=True, delta=0.0,
            detail="no non-ancestor placebo available in scope; vacuous pass"))

    # 2 ── random common cause
    scm2 = InstanceSCM(list(scm.nodes), {k: list(v) for k, v in scm.parents.items()},
                       dict(scm.leak), dict(scm.root_prior), list(scm.order))
    conf = "__confounder__"
    scm2.nodes = scm2.nodes + [conf]
    scm2.root_prior[conf] = 0.3
    scm2.leak[conf] = 0.0
    scm2.parents[x] = scm2.parents.get(x, []) + [(conf, 0.2)]
    sla_nodes = [p for p, _ in scm2.parents.get(OUTCOME, [])]
    if sla_nodes:
        first_sla = sla_nodes[0]
        scm2.parents[first_sla] = scm2.parents.get(first_sla, []) + [(conf, 0.2)]
    scm2.order = [conf] + list(scm.order)
    scm2.index = {n: i for i, n in enumerate(scm2.order)}
    pn_conf, _, _ = probability_of_necessity(scm2, evidence, x, n=fast_n,
                                             seed=int(rng.integers(1e6)))
    passed = abs(pn_conf - pn) <= 0.20
    results.append(RefutationResult(
        refuter="random_common_cause", passed=passed,
        delta=round(pn_conf - pn, 4),
        detail=f"PN moved {pn:.2f} → {pn_conf:.2f} under a synthetic confounder"))

    # 3 ── evidence subset
    observed_on = [v for v, val in evidence.items()
                   if val == 1 and v not in (x, OUTCOME)]
    deltas = []
    for r in range(3):
        keep = set(rng.choice(observed_on, size=max(1, int(0.6 * len(observed_on))),
                              replace=False)) if observed_on else set()
        sub_evidence = {v: val for v, val in evidence.items()
                        if v in keep or v in (x, OUTCOME) or val == 0}
        pn_sub, _, _ = probability_of_necessity(scm, sub_evidence, x, n=fast_n,
                                                seed=int(rng.integers(1e6)))
        deltas.append(pn_sub - pn)
    max_delta = max(abs(d) for d in deltas) if deltas else 0.0
    passed = max_delta <= 0.35
    results.append(RefutationResult(
        refuter="evidence_subset", passed=passed, delta=round(max_delta, 4),
        detail=f"max |ΔPN| over 3 evidence subsets = {max_delta:.2f}"))

    return results
