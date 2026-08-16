"""Topology-constrained temporal causal discovery over event types.

The THP insight, operationalized: the physical topology is a hard prior. An
excitation edge j -> k is only considered if instances of j and k were
topology-adjacent (or co-located) with positive lag inside some incident.
This collapses the search space from O(T^2) unconstrained pairs to the pairs
the physics allows, which is the single biggest accuracy lever available.

Edge score: excess conditional probability  P(k | j fired earlier, adjacent)
minus P(k | j absent), with a one-sided binomial significance check, then
stability selection (Meinshausen-Buhlmann) over bootstrap resamples of the
incident corpus. Direction comes from temporal precedence.

Assumptions: cascades respect topology; lags under DISCOVERY_WINDOW.
Breaks when: hidden shared causes act across non-adjacent elements (shared
power, conduit, timing). Mitigation: power feeds are modeled as explicit
nodes when inventory exists; where they don't, the adjudicator falls back to
Tian-Pearl bounds instead of point estimates.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
from scipy import stats

from ..config import STABILITY_BOOTSTRAPS, STABILITY_KEEP
from ..models import Event, TopoEdge

# physical-industry mechanisms (thermal runaway, buffer starvation) have
# minute-scale lags; the window must admit them or the graph loses its slowest
# and most dangerous edges
WINDOW_S = 900.0
MIN_STRENGTH = 0.15
MIN_PVALUE = 0.05


def orientation_allowed(src: str, dst: str) -> bool:
    """Domain orientation priors, applied like the topology prior.

    Service-layer events are customer-visible symptoms — they never cause
    infrastructure events. Config pushes are exogenous interventions — nothing
    in the network causes them. Encoding this is not a heuristic shortcut; it
    is the same move as THP's topology constraint: spend statistical power
    only on structures the physics permits.
    """
    if src.startswith("svc.") and not dst.startswith("svc."):
        return False
    if dst == "cfg.push":
        return False
    return True


@dataclass
class DiscoveredEdge:
    src_type: str
    dst_type: str
    strength: float
    stability: float
    lag_lo_ms: int
    lag_hi_ms: int
    method: str = "topo-hawkes"
    provenance: str = "learned"
    pinned_by: str = ""
    pinned_reason: str = ""

    def to_row(self) -> dict:
        return {
            "src_type": self.src_type, "dst_type": self.dst_type,
            "strength": round(self.strength, 4), "stability": round(self.stability, 3),
            "lag_lo_ms": self.lag_lo_ms, "lag_hi_ms": self.lag_hi_ms,
            "method": self.method, "provenance": self.provenance,
            "pinned_by": self.pinned_by, "pinned_reason": self.pinned_reason,
        }


@dataclass
class IncidentObs:
    """Per-incident canonical instances: (ts, entity, type), deduplicated."""
    instances: list[tuple[float, str, str]] = field(default_factory=list)
    types: set[str] = field(default_factory=set)


def _dedupe(events: list[Event]) -> IncidentObs:
    firsts: dict[tuple[str, str], float] = {}
    for e in sorted(events, key=lambda x: x.ts):
        k = (e.entity_id, e.event_type)
        if k not in firsts:
            firsts[k] = e.ts
    obs = IncidentObs()
    obs.instances = sorted(((ts, ent, et) for (ent, et), ts in firsts.items()))
    obs.types = {et for _, _, et in obs.instances}
    return obs


class AdjacencyIndex:
    """Directed propagation adjacency from the bi-temporal topology."""

    def __init__(self, topo: list[TopoEdge]):
        self.fwd: dict[str, set[str]] = defaultdict(set)
        for e in topo:
            self.fwd[e.src].add(e.dst)
            if e.relation == "peers":
                self.fwd[e.dst].add(e.src)

    def linked(self, a: str, b: str) -> bool:
        return a == b or b in self.fwd.get(a, ()) or a in self.fwd.get(b, ())

    def directed(self, a: str, b: str) -> bool:
        """Propagation strictly along topology direction (for same-type chains)."""
        return b in self.fwd.get(a, ())


class CausalDiscovery:
    def __init__(self, adjacency: AdjacencyIndex,
                 sink_types: set[str] | None = None,
                 exogenous_types: set[str] | None = None):
        self.adj = adjacency
        self.sinks = sink_types or set()
        self.exogenous = exogenous_types or set()

    def _orientation_ok(self, src: str, dst: str) -> bool:
        if not orientation_allowed(src, dst):
            return False
        if src in self.sinks and dst not in self.sinks:
            return False               # customer-visible symptoms are sinks
        if dst in self.exogenous:
            return False               # change events are exogenous interventions
        return True

    def _pair_stats(self, corpus: list[IncidentObs]
                    ) -> tuple[dict, dict, dict, dict]:
        n_with: dict[str, int] = defaultdict(int)          # incidents containing j
        n_pair: dict[tuple[str, str], int] = defaultdict(int)   # j precedes k, adjacent
        n_k_without_j: dict[tuple[str, str], int] = defaultdict(int)
        lags: dict[tuple[str, str], list[float]] = defaultdict(list)
        all_types: set[str] = set()
        for obs in corpus:
            all_types |= obs.types
        for obs in corpus:
            for t in obs.types:
                n_with[t] += 1
            fired_pairs: set[tuple[str, str]] = set()
            for i, (ts_i, ent_i, t_i) in enumerate(obs.instances):
                for ts_p, ent_p, t_p in obs.instances[:i]:
                    dt = ts_i - ts_p
                    if (0 < dt <= WINDOW_S
                            and (t_p != t_i or ent_p != ent_i)
                            and (self.adj.linked(ent_p, ent_i) if t_p != t_i
                                 else self.adj.directed(ent_p, ent_i))):
                        if (t_p, t_i) not in fired_pairs:
                            fired_pairs.add((t_p, t_i))
                            n_pair[(t_p, t_i)] += 1
                        lags[(t_p, t_i)].append(dt)
            for j in all_types:
                if j not in obs.types:
                    for k in obs.types:
                        n_k_without_j[(j, k)] += 1
        return n_with, n_pair, n_k_without_j, lags

    def _edges_once(self, corpus: list[IncidentObs]) -> dict[tuple[str, str], DiscoveredEdge]:
        n_with, n_pair, n_k_wo, lags = self._pair_stats(corpus)
        n_total = len(corpus)
        out: dict[tuple[str, str], DiscoveredEdge] = {}
        for (j, k), cnt in n_pair.items():
            if not self._orientation_ok(j, k):
                continue
            nj = n_with[j]
            if nj < 3:
                continue
            p_k_given_j = cnt / nj
            n_wo = n_total - nj
            p_k_wo_j = (n_k_wo[(j, k)] / n_wo) if n_wo > 0 else 0.0
            strength = p_k_given_j - p_k_wo_j
            if strength < MIN_STRENGTH:
                continue
            # one-sided binomial test: is cnt/nj significantly above base rate?
            base = min(max(p_k_wo_j, 1e-6), 1 - 1e-6)
            pval = stats.binomtest(cnt, nj, base, alternative="greater").pvalue
            if pval > MIN_PVALUE:
                continue
            ls = lags[(j, k)]
            out[(j, k)] = DiscoveredEdge(
                src_type=j, dst_type=k, strength=float(strength), stability=1.0,
                lag_lo_ms=int(np.percentile(ls, 10) * 1000),
                lag_hi_ms=int(np.percentile(ls, 90) * 1000))
        return out

    def discover(self, incidents_events: list[list[Event]],
                 seed: int = 3) -> list[DiscoveredEdge]:
        corpus = [_dedupe(evs) for evs in incidents_events if evs]
        if not corpus:
            return []
        rng = np.random.default_rng(seed)
        full = self._edges_once(corpus)
        counts: dict[tuple[str, str], int] = defaultdict(int)
        for _ in range(STABILITY_BOOTSTRAPS):
            sample = [corpus[i] for i in
                      rng.integers(0, len(corpus), size=len(corpus))]
            for key in self._edges_once(sample):
                counts[key] += 1
        kept: list[DiscoveredEdge] = []
        for key, edge in full.items():
            stability = counts[key] / STABILITY_BOOTSTRAPS
            if stability >= STABILITY_KEEP:
                edge.stability = float(stability)
                kept.append(edge)
        # break 2-cycles: keep the direction with higher strength*stability
        by_key = {(e.src_type, e.dst_type): e for e in kept}
        final: list[DiscoveredEdge] = []
        for (j, k), e in by_key.items():
            if j != k:
                rev = by_key.get((k, j))
                if rev is not None and (rev.strength * rev.stability
                                        > e.strength * e.stability):
                    continue
            final.append(e)
        return final


def correlation_baseline(incidents_events: list[list[Event]]
                         ) -> list[tuple[str, str, float]]:
    """Co-occurrence correlation graph — the naive competitor for evaluation."""
    corpus = [_dedupe(evs) for evs in incidents_events if evs]
    n = len(corpus)
    if n == 0:
        return []
    types = sorted({t for obs in corpus for t in obs.types})
    idx = {t: i for i, t in enumerate(types)}
    M = np.zeros((n, len(types)))
    for r, obs in enumerate(corpus):
        for t in obs.types:
            M[r, idx[t]] = 1.0
    C = np.corrcoef(M.T)
    out = []
    for i, a in enumerate(types):
        for j, b in enumerate(types):
            if i < j and np.isfinite(C[i, j]) and C[i, j] > 0.3:
                out.append((a, b, float(C[i, j])))
    return out
