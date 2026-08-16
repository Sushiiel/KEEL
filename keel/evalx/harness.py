"""Evaluation harness: the report that convinces a buyer.

Retrospective replay over the resolved-incident corpus, KEEL vs. two
baselines, with localization metrics (HR@1/HR@3/MRR), empirical conformal
coverage vs. nominal (marginal and per-stratum), and the selective-prediction
risk-coverage curve. Every number is falsifiable against stored incidents.

Baselines:
  severity-first     rank candidate instances by (severity, onset time)
  corr+pagerank      correlation graph over the corpus + personalized PageRank
                     from the incident's symptom types (the published
                     neural-Granger+PPR shape, simplified)
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Any, Optional

import networkx as nx
import numpy as np

from ..calibrate.conformal import conformal_quantile, corpus, empirical_coverage
from ..config import CONFORMAL_ALPHA
from ..hypothesis.evidence import dedupe_instances
from ..pipeline import Runtime, score_incident
from ..structure.discovery import correlation_baseline

REPORT_KEY = "eval_report"


def _rank_metrics(ranks: list[Optional[int]]) -> dict[str, float]:
    n = len(ranks) or 1
    hr1 = sum(1 for r in ranks if r == 1) / n
    hr3 = sum(1 for r in ranks if r is not None and r <= 3) / n
    mrr = sum(1 / r for r in ranks if r is not None) / n
    return {"hr1": round(hr1, 4), "hr3": round(hr3, 4), "mrr": round(mrr, 4),
            "n": len(ranks)}


def _severity_rank(rt: Runtime, incident, truth: str) -> Optional[int]:
    events = rt.store.events_for(incident.incident_id)
    insts = dedupe_instances(events)
    cands = [i for i in insts if not i["type"].startswith("svc.")]
    cands.sort(key=lambda i: (i["severity"], i["ts"]))
    for pos, c in enumerate(cands[:10], start=1):
        if c["variable"] == truth:
            return pos
    return None


def _pagerank_rank(rt: Runtime, incident, truth: str,
                   corr_graph: nx.Graph) -> Optional[int]:
    events = rt.store.events_for(incident.incident_id)
    insts = dedupe_instances(events)
    present_types = {i["type"] for i in insts}
    symptoms = {t for t in present_types if t.startswith("svc.")} or present_types
    personalization = {n: (1.0 if n in symptoms else 0.01)
                       for n in corr_graph.nodes}
    if not corr_graph.nodes:
        return None
    pr = nx.pagerank(corr_graph, personalization=personalization, alpha=0.85)
    cands = [i for i in insts if not i["type"].startswith("svc.")]
    cands.sort(key=lambda i: (-pr.get(i["type"], 0.0), i["ts"]))
    for pos, c in enumerate(cands[:10], start=1):
        if c["variable"] == truth:
            return pos
    return None


def run_replay(rt: Runtime, holdout_frac: float = 0.35) -> dict[str, Any]:
    t0 = time.time()
    resolved = [i for i in rt.store.incidents(limit=400)
                if i.status == "resolved" and i.ground_truth]
    resolved.sort(key=lambda i: i.t0)
    n_hold = max(10, int(len(resolved) * holdout_frac))
    holdout = resolved[-n_hold:]

    corr = correlation_baseline(
        [rt.store.events_for(i.incident_id) for i in resolved[:-n_hold]])
    corr_graph = nx.Graph()
    for a, b, w in corr:
        corr_graph.add_edge(a, b, weight=w)

    keel_ranks: list[Optional[int]] = []
    sev_ranks: list[Optional[int]] = []
    ppr_ranks: list[Optional[int]] = []
    keel_scores: list[tuple[float, bool]] = []   # (top score, top correct)
    coverage_hits: list[int] = []
    per_stratum: dict[str, list[int]] = defaultdict(list)
    abstained = 0

    cal_scores = [1 - c["score_true"] for c in corpus(rt.store)]
    q_hat = conformal_quantile(cal_scores, CONFORMAL_ALPHA)

    # all methods are compared on the SAME scored subset; incidents where KEEL
    # abstains (no customer-visible outage, insufficient evidence) are reported
    # separately — hiding them would flatter nobody honestly
    for inc in holdout:
        truth = inc.ground_truth or ""
        res = score_incident(rt, inc, fast=True)
        if res is None:
            abstained += 1
            continue
        ranked = res["ranked"]
        rank = ranked.index(truth) + 1 if truth in ranked else None
        keel_ranks.append(rank)
        top_var = ranked[0]
        keel_scores.append((res["scores"][top_var], rank == 1))
        if q_hat is not None:
            inside = [v for v, s in res["scores"].items()
                      if (1 - s) <= q_hat]
            hit = 1 if truth in inside else 0
            coverage_hits.append(hit)
            per_stratum[res["stratum"]].append(hit)
        sev_ranks.append(_severity_rank(rt, inc, truth))
        ppr_ranks.append(_pagerank_rank(rt, inc, truth, corr_graph))

    # risk-coverage: sweep confidence threshold over kept top-1 decisions
    keel_scores.sort(key=lambda x: -x[0])
    risk_cov = []
    for frac in np.linspace(0.2, 1.0, 9):
        k = max(1, int(len(keel_scores) * frac))
        kept = keel_scores[:k]
        acc = sum(1 for _, ok in kept if ok) / len(kept)
        risk_cov.append({"coverage": round(float(frac), 2),
                         "accuracy": round(acc, 4)})

    report = {
        "generated_at": time.time(),
        "holdout_n": len(holdout),
        "replay_seconds": round(time.time() - t0, 1),
        "keel": _rank_metrics(keel_ranks),
        "baseline_severity": _rank_metrics(sev_ranks),
        "baseline_corr_pagerank": _rank_metrics(ppr_ranks),
        "coverage": {
            "nominal": round(1 - CONFORMAL_ALPHA, 3),
            "empirical": (round(float(np.mean(coverage_hits)), 4)
                          if coverage_hits else None),
            "per_stratum": {k: {"coverage": round(float(np.mean(v)), 4),
                                "n": len(v)}
                            for k, v in per_stratum.items()},
        },
        "loo_coverage": empirical_coverage(rt.store),
        "risk_coverage": risk_cov,
        "abstention_rate": round(abstained / max(len(holdout), 1), 4),
        "beats_baseline_by": None,
    }
    delta = report["keel"]["hr1"] - report["baseline_corr_pagerank"]["hr1"]
    report["beats_baseline_by"] = round(delta, 4)
    rt.store.kv_set(REPORT_KEY, report)
    return report


def cached_report(rt: Runtime) -> Optional[dict[str, Any]]:
    return rt.store.kv_get(REPORT_KEY)
