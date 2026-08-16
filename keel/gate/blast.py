"""Blast radius: weighted reachability from the action's touched elements.

R(a) = { v : exists path u ~> v, u in touched, w(path) > theta }, where hop
weights come from the dependency relation class. Mapped to services and SLAs
via the service-topology layer: a service's SLA is at risk when the action
leaves it without a healthy path.
"""
from __future__ import annotations

from collections import defaultdict

from ..models import BlastRadius, RemediationAction, TopoEdge
from ..store import Store
from ..substrate.world import service_customers, service_paths

HOP_W = {"carries": 0.7, "feeds": 0.75, "serves": 0.9, "peers": 0.35}
THETA = 0.15

def hard_failed_elements(evidence: dict[str, int], hard_types: set[str]) -> set[str]:
    """Elements truly removed from the flow path — the domain pack declares
    which event types kill an element; churn and degradation do not."""
    out = set()
    for var, val in evidence.items():
        if val != 1 or "|" not in var:
            continue
        ent, _, et = var.partition("|")
        if et in hard_types:
            out.add(ent)
    return out


def compute_blast(store: Store, topo: list[TopoEdge],
                  action: RemediationAction,
                  failed_elements: set[str]) -> BlastRadius:
    fwd: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for e in topo:
        w = HOP_W.get(e.relation, 0.5)
        fwd[e.src].append((e.dst, w))
        if e.relation == "peers":
            fwd[e.dst].append((e.src, w))

    svc_ids = {e.entity_id for e in store.entities() if e.kind == "service"}
    reach: dict[str, float] = {}
    frontier = [(el, 1.0) for el in action.target_entities]
    while frontier:
        node, w = frontier.pop()
        if w < THETA or reach.get(node, 0.0) >= w:
            continue
        reach[node] = w
        for nxt, hw in fwd.get(node, []):
            frontier.append((nxt, w * hw))

    elements = sorted(n for n in reach if n not in svc_ids)
    services = sorted(n for n in reach if n in svc_ids)

    # SLA risk: after the action, does each touched service still have a path?
    paths = service_paths(store)
    customers = service_customers(store)
    drained = set(action.target_entities) if action.parameters.get("drain") else set()
    dead = failed_elements | drained
    at_risk = []
    for svc in services:
        alive = [p for p in paths.get(svc, []) if not any(el in dead for el in p)]
        if not alive:
            at_risk.append(svc)

    return BlastRadius(
        elements=elements, services=services, slas_at_risk=len(at_risk),
        customers_affected=sum(customers.get(s, 0) for s in at_risk))
