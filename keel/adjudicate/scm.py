"""Structural causal model over incident instances.

The learned type-level DAG is instantiated onto the incident's entities using
the bi-temporal topology (never present-day topology for a past incident).
Mechanisms are noisy-OR:

    P(X_v = 1 | pa(v)) = 1 - (1 - leak_v) * prod_{u in pa(v), X_u=1} (1 - w_uv)

which admits an explicit exogenous-noise representation — per-edge activation
Bernoullis and per-node leaks — so Pearl's abduction/action/prediction runs
exactly: with all instance variables observed, the noise posterior factorizes
per node and abduction is exact conditional sampling, no MCMC required.

Assumptions: binary instance states; independent edge noises (noisy-OR);
cascades respect the learned DAG. Breaks when: mechanisms are conjunctive
(AND-like) or inhibitory — the refuters and the drift gate are the backstop.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

import numpy as np

from ..models import Event, TopoEdge
from ..structure.discovery import AdjacencyIndex

OUTCOME = "OUTAGE"
DEFAULT_LEAK = 0.02
ROOT_PRIOR = 0.03


def split_var(v: str) -> tuple[str, str]:
    ent, _, et = v.partition("|")
    return ent, et


class InstanceSCM:
    def __init__(self, nodes: list[str], parents: dict[str, list[tuple[str, float]]],
                 leak: dict[str, float], root_prior: dict[str, float],
                 order: list[str]):
        self.nodes = nodes
        self.parents = parents
        self.leak = leak
        self.root_prior = root_prior
        self.order = order
        self.index = {n: i for i, n in enumerate(order)}

    # ── construction ─────────────────────────────────────────────────────────
    @classmethod
    def build(cls, type_edges: list[dict], topo: list[TopoEdge],
              observed_vars: set[str], entities: list[str],
              outage_types: set[str] = frozenset({"svc.sla_breach"}),
              degradation_types: set[str] = frozenset({"svc.latency_high"}),
              ) -> "InstanceSCM":
        adj = AdjacencyIndex(topo)
        w_type: dict[tuple[str, str], float] = {
            (r["src_type"], r["dst_type"]): max(0.05, min(0.98, float(r["strength"])))
            for r in type_edges}
        ents = set(entities)
        # variable scope: everything observed, plus latent-capable instances of
        # parent types on incident entities (so interventions can propagate)
        variables: set[str] = set(observed_vars)
        obs_by_type: dict[str, list[str]] = defaultdict(list)
        for v in observed_vars:
            ent, et = split_var(v)
            obs_by_type[et].append(ent)

        parents: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for v in sorted(variables):
            ent_v, et_v = split_var(v)
            for (j, k), w in w_type.items():
                if k != et_v:
                    continue
                for ent_u in ents:
                    u = f"{ent_u}|{j}"
                    if u == v or u not in variables:
                        continue
                    # same-type propagation follows topology direction only,
                    # so chain cascades never instantiate as 2-cycles
                    ok = (adj.directed(ent_u, ent_v) if j == k
                          else adj.linked(ent_u, ent_v))
                    if ok:
                        parents[v].append((u, w))

        # synthetic outcome node: customer-visible degradation, using the
        # domain pack's canonical impact vocabulary
        nodes = sorted(variables) + [OUTCOME]
        for v in variables:
            _, _, vt = v.partition("|")
            if vt in outage_types:
                parents[OUTCOME].append((v, 0.99))
            elif vt in degradation_types:
                parents[OUTCOME].append((v, 0.80))

        leak = {n: (0.0 if n == OUTCOME else DEFAULT_LEAK) for n in nodes}
        root_prior = {n: ROOT_PRIOR for n in nodes if not parents.get(n)}
        order = cls._toposort(nodes, parents)
        return cls(nodes, dict(parents), leak, root_prior, order)

    @staticmethod
    def _toposort(nodes: list[str], parents: dict[str, list[tuple[str, float]]]
                  ) -> list[str]:
        """Kahn's algorithm. Instantiation cycles are broken at the weakest
        edge INSIDE a strongly-connected component — never by sacrificing an
        innocent downstream edge that merely waits on the cycle."""
        import networkx as nx

        g = nx.DiGraph()
        g.add_nodes_from(nodes)
        for n, plist in parents.items():
            for p, w in plist:
                g.add_edge(p, n, weight=w)
        changed = True
        while changed:
            changed = False
            for scc in list(nx.strongly_connected_components(g)):
                if len(scc) < 2:
                    continue
                weakest = min(((u, v, d["weight"]) for u, v, d in
                               g.edges(scc, data=True) if u in scc and v in scc),
                              key=lambda x: x[2])
                u, v, _ = weakest
                g.remove_edge(u, v)
                parents[v] = [(q, w) for q, w in parents.get(v, []) if q != u]
                changed = True

        pa = {n: list(parents.get(n, [])) for n in nodes}
        order: list[str] = []
        remaining = set(nodes)
        while remaining:
            free = [n for n in remaining
                    if all(p not in remaining for p, _ in pa[n])]
            assert free, "cycle survived SCC breaking"
            for n in sorted(free):
                order.append(n)
                remaining.discard(n)
        return order

    # ── abduction: exact per-node noise posterior under full observation ─────
    def abduct(self, evidence: dict[str, int], n: int,
               rng: np.random.Generator) -> dict:
        """Sample exogenous noise U consistent with the observed incident.

        Returns dict with per-node edge-noise matrices (n x #parents) and leak
        vectors. Nodes absent from `evidence` are treated generatively (their
        value is simulated), which is exact when they have no observed
        descendants and an approximation otherwise (used only by refuters).
        """
        U_edge: dict[str, np.ndarray] = {}
        U_leak: dict[str, np.ndarray] = {}
        state: dict[str, np.ndarray] = {}

        for v in self.order:
            pa = self.parents.get(v, [])
            k = len(pa)
            w = np.array([wj for _, wj in pa]) if k else np.zeros(0)
            pstate = (np.stack([state[p] for p, _ in pa], axis=1)
                      if k else np.zeros((n, 0)))
            leak_p = self.leak.get(v, DEFAULT_LEAK)
            if not pa:
                prior = self.root_prior.get(v, ROOT_PRIOR)
                if v in evidence:
                    val = np.full(n, evidence[v], dtype=np.int8)
                    U_leak[v] = val.astype(np.float64)
                else:
                    U_leak[v] = (rng.random(n) < prior).astype(np.float64)
                    val = U_leak[v].astype(np.int8)
                U_edge[v] = np.zeros((n, 0))
                state[v] = val
                continue

            u = (rng.random((n, k)) < w[None, :]).astype(np.float64)
            ul = (rng.random(n) < leak_p).astype(np.float64)
            active = u * pstate                      # noise fires only if parent on
            fired = (active.max(axis=1, initial=0.0) + ul) > 0

            if v in evidence:
                obs = evidence[v]
                if obs == 0:
                    # every on-parent edge noise and the leak must be 0
                    u = u * (1 - pstate)             # zero where parent on
                    ul = np.zeros(n)
                    val = np.zeros(n, dtype=np.int8)
                else:
                    # condition on OR = 1: force one cause where none fired,
                    # chosen proportionally to the available cause weights
                    need = ~fired
                    if need.any():
                        rows = np.where(need)[0]
                        on_w = w[None, :] * pstate[rows]         # available causes
                        totals = on_w.sum(axis=1) + leak_p
                        pick = rng.random(rows.size) * totals
                        cum = np.cumsum(on_w, axis=1)
                        lower = np.hstack([np.zeros((rows.size, 1)), cum[:, :-1]])
                        chosen = (pick[:, None] > lower) & (pick[:, None] <= cum)
                        u[rows] = np.maximum(u[rows], chosen.astype(np.float64))
                        take_leak = pick > cum[:, -1]
                        ul[rows[take_leak]] = 1.0
                    val = np.ones(n, dtype=np.int8)
            else:
                val = fired.astype(np.int8)

            U_edge[v], U_leak[v], state[v] = u, ul, val

        return {"edge": U_edge, "leak": U_leak, "factual": state}

    # ── prediction: deterministic forward pass given noise ───────────────────
    def forward(self, U: dict, interventions: dict[str, int],
                n: int) -> dict[str, np.ndarray]:
        state: dict[str, np.ndarray] = {}
        for v in self.order:
            if v in interventions:
                state[v] = np.full(n, interventions[v], dtype=np.int8)
                continue
            pa = self.parents.get(v, [])
            if not pa:
                state[v] = U["leak"][v].astype(np.int8)
                continue
            pstate = np.stack([state[p] for p, _ in pa], axis=1)
            active = U["edge"][v] * pstate
            fired = (active.max(axis=1, initial=0.0) + U["leak"][v]) > 0
            state[v] = fired.astype(np.int8)
        return state


def evidence_from_events(events: list[Event], scm_nodes: list[str],
                         impact_types: set[str] = frozenset(
                             {"svc.sla_breach", "svc.latency_high"})) -> dict[str, int]:
    # duplicates collapse to one instance variable; suppression only affects
    # display and hypothesis prioritization, never the evidence itself
    fired = {f"{e.entity_id}|{e.event_type}" for e in events}
    ev = {}
    for v in scm_nodes:
        if v == OUTCOME:
            continue
        ev[v] = 1 if v in fired else 0
    ev[OUTCOME] = 1 if any(v.partition("|")[2] in impact_types for v in fired) else 0
    return ev
