"""Split conformal prediction over hypothesis rankings, with Mondrian strata.

Nonconformity score: s = 1 - p_hat(true root | incident), where p_hat is the
normalized adjudication score of the true root among candidates. Calibrated
on this tenant's own resolved incidents:

    q_hat = Quantile_{ceil((n+1)(1-alpha))/n}(s_1..s_n)
    C(x)  = { h : 1 - p_hat(h) <= q_hat }        =>  P(true in C) >= 1 - alpha

Mondrian (group-conditional) calibration by root-cause layer keeps coverage
honest per stratum — marginal coverage that hides 60% coverage on optical
faults is a lie the customer will be held to.

Assumptions: exchangeability of calibration and test incidents. Breaks under
distribution shift — which is why the drift gate can force abstention, and
why below MIN_CALIBRATION_N the calibrator refuses to certify at all.
"""
from __future__ import annotations

import math
from typing import Any, Optional

from ..config import CONFORMAL_ALPHA, MIN_CALIBRATION_N
from ..store import Store

CORPUS_KEY = "calibration_corpus"   # list of {incident_id, stratum, score_true, ts}


def add_calibration_example(store: Store, incident_id: str, stratum: str,
                            score_true: float, ts: float) -> None:
    corpus = store.kv_get(CORPUS_KEY, [])
    corpus = [c for c in corpus if c["incident_id"] != incident_id]
    corpus.append({"incident_id": incident_id, "stratum": stratum,
                   "score_true": round(float(score_true), 5), "ts": ts})
    store.kv_set(CORPUS_KEY, corpus)


def corpus(store: Store) -> list[dict[str, Any]]:
    return store.kv_get(CORPUS_KEY, [])


def conformal_quantile(scores: list[float], alpha: float) -> Optional[float]:
    """Finite-sample-corrected quantile of nonconformity scores."""
    n = len(scores)
    if n == 0:
        return None
    rank = math.ceil((n + 1) * (1 - alpha))
    if rank > n:
        return None                     # not enough data for this alpha
    return sorted(scores)[rank - 1]


def conformal_set(store: Store, candidate_scores: dict[str, float],
                  stratum: str = "", alpha: float = CONFORMAL_ALPHA,
                  widen: float = 0.0) -> dict[str, Any]:
    """Return the prediction set over candidate hypothesis ids.

    candidate_scores: hypothesis_id -> normalized score (sums to ~1).
    widen: additive inflation of q_hat commanded by the drift gate.
    """
    corp = corpus(store)
    n_all = len(corp)
    if n_all < MIN_CALIBRATION_N:
        return {"set": [], "q_hat": None, "n": n_all, "strata": "insufficient",
                "abstain_reason": f"calibration corpus has {n_all} examples; "
                                  f"minimum is {MIN_CALIBRATION_N}"}

    strat_scores = [1 - c["score_true"] for c in corp if c["stratum"] == stratum]
    if stratum and len(strat_scores) >= MIN_CALIBRATION_N:
        q = conformal_quantile(strat_scores, alpha)
        used, n_used = f"mondrian:{stratum}", len(strat_scores)
    else:
        q = conformal_quantile([1 - c["score_true"] for c in corp], alpha)
        used, n_used = "marginal", n_all
    if q is None:
        return {"set": [], "q_hat": None, "n": n_used, "strata": used,
                "abstain_reason": "quantile undefined at requested alpha"}

    q_eff = min(1.0, q + widen)
    inside = [h for h, s in candidate_scores.items() if (1 - s) <= q_eff]
    return {"set": inside, "q_hat": round(q_eff, 4), "n": n_used,
            "strata": used, "abstain_reason": None}


def empirical_coverage(store: Store, alpha: float = CONFORMAL_ALPHA
                       ) -> dict[str, Any]:
    """Leave-one-out empirical coverage of the current corpus, per stratum."""
    corp = corpus(store)
    if len(corp) < 5:
        return {"marginal": None, "per_stratum": {}, "n": len(corp)}
    per: dict[str, list[int]] = {}
    hits = []
    for i, c in enumerate(corp):
        rest = [1 - d["score_true"] for j, d in enumerate(corp) if j != i]
        q = conformal_quantile(rest, alpha)
        hit = 1 if q is not None and (1 - c["score_true"]) <= q else 0
        hits.append(hit)
        per.setdefault(c["stratum"], []).append(hit)
    return {
        "marginal": round(sum(hits) / len(hits), 4),
        "nominal": round(1 - alpha, 3),
        "per_stratum": {k: {"coverage": round(sum(v) / len(v), 4), "n": len(v)}
                        for k, v in per.items()},
        "n": len(corp),
    }
