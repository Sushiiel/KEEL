"""CMDP safety shield: constraint evaluation + projection to the nearest
feasible action.

'Can this be done safely' (physics) is decided here; 'is this permitted'
(organizational policy) is decided in gate.policy. The shield never asks the
agent — it evaluates the action against operational constraints and, on
violation, projects to the closest safer variant (e.g. reroute-without-drain)
before giving up. The system fails CLOSED.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Optional

from ..config import CMDP_LIMITS
from ..models import BlastRadius, RemediationAction, TopoEdge, TwinPrediction
from ..store import Store
from ..substrate.world import service_paths
from .blast import compute_blast


def evaluate_constraints(store: Store, action: RemediationAction,
                         blast: BlastRadius, twin: TwinPrediction,
                         failed_elements: set[str]) -> dict[str, float]:
    paths = service_paths(store)
    drained = set(action.target_entities) if action.parameters.get("drain") else set()
    dead = failed_elements | drained
    min_alive = 99
    for svc, plist in paths.items():
        if any(any(el in set(action.target_entities) for el in p) for p in plist):
            alive = sum(1 for p in plist if not any(el in dead for el in p))
            min_alive = min(min_alive, alive)
    return {
        "elements_touched": len(action.target_entities),
        "blast_radius_elements": len(blast.elements),
        "slas_at_risk": blast.slas_at_risk,
        "redundancy_min_paths": min_alive if min_alive != 99 else 1,
        "est_sla_minutes": round(twin.restore_minutes if blast.slas_at_risk else 0.0, 2),
    }


def violations(costs: dict[str, float]) -> list[str]:
    out = []
    for name, limit in CMDP_LIMITS.items():
        v = costs.get(name, 0)
        if name == "redundancy_min_paths":
            if v < limit:
                out.append(f"{name}: {v} < required {limit}")
        elif v > limit:
            out.append(f"{name}: {v} > limit {limit}")
    return out


def project_to_safe(store: Store, topo: list[TopoEdge],
                    action: RemediationAction, twin: TwinPrediction,
                    failed_elements: set[str]
                    ) -> Optional[tuple[RemediationAction, BlastRadius, dict]]:
    """Nearest feasible variant: currently, defer the drain (reroute only)."""
    if not action.parameters.get("drain"):
        return None
    safer = action.model_copy(deep=True)
    safer.parameters = dict(action.parameters)
    safer.parameters["drain"] = False
    safer.parameters["drain_deferred"] = "await maintenance window"
    safer.description = safer.description.replace("drain", "defer drain of")
    blast2 = compute_blast(store, topo, safer, failed_elements)
    costs2 = evaluate_constraints(store, safer, blast2, twin, failed_elements)
    if violations(costs2):
        return None
    return safer, blast2, costs2
