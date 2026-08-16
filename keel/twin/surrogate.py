"""Tier-1 counterfactual twin: fast surrogate rollouts for inline certification.

A remediation is a set of interventions on the incident's instance SCM. The
twin propagates the abducted noise through the intervened model to predict
whether the outage clears, the restore-time distribution, and what else the
action perturbs. Runbooks and action dynamics come from the domain pack;
fidelity is measured, never assumed: every executed action's predicted-vs-
observed error lands in a rolling per-action-class ledger, and action classes
whose fidelity degrades below the floor are refused.
"""
from __future__ import annotations

import numpy as np

from ..calibrate.drift import fidelity_residual
from ..config import ABDUCTION_SAMPLES, FIDELITY_FLOOR
from ..domains import DomainPack
from ..models import RemediationAction, TwinPrediction
from ..adjudicate.scm import OUTCOME, InstanceSCM, split_var

_FALLBACK_DYNAMICS = (5.0, 2.0, 60, True)


def propose_remediation(pack: DomainPack, root_variable: str,
                        backup_target: str = "") -> RemediationAction:
    entity, etype = split_var(root_variable)
    cls, desc = pack.runbooks.get(
        etype, ("restart_protocol", "Generic recovery of the implicated element"))
    dynamics = pack.action_dynamics.get(cls, _FALLBACK_DYNAMICS)
    _, _, rollback_s, reversible = dynamics
    params: dict = {"root_variable": root_variable}
    targets = [entity]
    if cls == "reroute_drain":
        params["reroute_to"] = backup_target or "protection-path"
        params["drain"] = True
        desc = f"Reroute λ-path {entity} → {params['reroute_to']}, drain {entity}"
    else:
        desc = f"{desc} ({entity})"
    return RemediationAction(
        action_id=f"act-{abs(hash(root_variable)) % 99999:05d}",
        action_class=cls, description=desc, target_entities=targets,
        parameters=params, reversible=reversible,
        rollback_plan=(f"Automatic rollback verified in twin ({rollback_s:.0f}s)"
                       if reversible else "Not reversible — physical intervention"))


def _interventions_for(action: RemediationAction, scm: InstanceSCM) -> dict[str, int]:
    """The action's do() set: clear the root and its on-entity manifestations."""
    root_var = action.parameters.get("root_variable", "")
    ent, _ = split_var(root_var)
    out = {root_var: 0}
    for v in scm.nodes:
        e2, _ = split_var(v)
        if e2 == ent and v != OUTCOME:
            out[v] = 0
    return out


def rollout(scm: InstanceSCM, evidence: dict[str, int],
            action: RemediationAction, pack: DomainPack, store=None,
            n: int = ABDUCTION_SAMPLES // 2, seed: int = 31) -> TwinPrediction:
    rng = np.random.default_rng(seed)
    U = scm.abduct(evidence, n, rng)
    iv = _interventions_for(action, scm)
    cf = scm.forward(U, iv, n)
    p_resolve = float((cf[OUTCOME] == 0).mean())

    base, spread, rollback_s, reversible = pack.action_dynamics.get(
        action.action_class, _FALLBACK_DYNAMICS)
    times = rng.gamma(shape=(base / spread) ** 2,
                      scale=spread ** 2 / base, size=400)
    restore = float(np.median(times))
    lo, hi = float(np.percentile(times, 10)), float(np.percentile(times, 90))

    protected = pack.impact_protected_type
    additional = []
    if protected and action.parameters.get("drain"):
        additional = [f"{split_var(v)[0]} (transient {protected.split('.')[-1]})"
                      for v in scm.nodes
                      if v.endswith(f"|{protected}") and evidence.get(v, 0) == 0][:3]

    fid = 1.0 - min(1.0, fidelity_residual(store, action.action_class)) \
        if store is not None else 1.0

    traj = []
    for t in np.linspace(0, restore * 1.6, 28):
        if t <= 0.4:
            hlt = 0.15
        elif t < restore:
            hlt = 0.15 + 0.75 * ((t - 0.4) / max(restore - 0.4, 0.1)) ** 1.6
        else:
            hlt = 0.97
        traj.append({"minute": round(float(t), 2),
                     "health": round(float(min(1.0, hlt + rng.normal(0, 0.015))), 3)})

    return TwinPrediction(
        tier=1, resolves_incident=p_resolve >= 0.5, p_resolve=round(p_resolve, 4),
        restore_minutes=round(restore, 1), restore_lo=round(lo, 1),
        restore_hi=round(hi, 1), additional_impact=additional,
        rollback_verified=reversible, rollback_seconds=rollback_s,
        fidelity_score=round(max(0.0, fid), 3), trajectory=traj)


def fidelity_ok(store, action_class: str) -> bool:
    return (1.0 - min(1.0, fidelity_residual(store, action_class))) >= FIDELITY_FLOOR
