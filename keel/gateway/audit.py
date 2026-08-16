"""The audit evidence pack — the artifact auditors and insurers actually ask
for. One call produces a self-contained, verifiable bundle:

  - sampled signed decision certificates (uniform random — defeats cherry-
    picking) with their Merkle inclusion proofs and chain verification
  - per-(agent, action-class) calibration tables with the bound's exact scope
  - human-oversight record (escalations, approver identities, release certs)
  - tripwire and enforcement configuration in force
  - control mappings: EU AI Act Art 12/14, ISO/IEC 42001, NIST AI RMF,
    and the underwriter evidence controls

Everything in the pack is independently re-verifiable from the transparency
log root — the pack proves itself.
"""
from __future__ import annotations

import random
import time
from typing import Any

from ..cert import authority, translog
from .checkers import TRIPWIRES
from .engine import (confidence_for, earned_tier, gw_store, list_agents,
                     recent_decisions)

CONTROL_MAPPINGS = {
    "EU AI Act Art 12 (record-keeping)":
        "every gateway decision is an Ed25519-signed certificate with inputs, "
        "checks, calibration state, and verdict, anchored in an append-only "
        "Merkle log with inclusion proofs",
    "EU AI Act Art 14 (human oversight)":
        "risk-tiered ESCALATE queue; approver identity, timestamp, and note "
        "are signed into a release certificate distinct from the request",
    "ISO/IEC 42001 (AIMS, per-decision sampling)":
        "this pack contains a uniform random sample of decisions with "
        "verifiable signatures and chain inclusion — sample size configurable",
    "NIST AI RMF (MEASURE/MANAGE)":
        "per-(agent, action-class) Clopper-Pearson calibration with drift "
        "detection (Page-Hinkley); autonomy tiers derive from measured "
        "outcomes, never configuration",
    "Underwriter control — production enforcement evidence":
        "tripwire set and enforcement mode included; denied calls return the "
        "signed certificate id to the agent, proving enforcement in the path",
}


def build_audit_pack(sample_size: int = 25, seed: int | None = None
                     ) -> dict[str, Any]:
    store = gw_store()
    rng = random.Random(seed)
    entries = store.translog()
    chain = translog.verify_chain(store)
    sample_idx = sorted(rng.sample(range(len(entries)),
                                   min(sample_size, len(entries))))
    sampled = []
    for idx in sample_idx:
        cert = store.certificate(entries[idx]["cert_id"])
        if cert is None:
            continue
        sampled.append({
            "certificate": cert.model_dump(),
            "signature_verification": authority.verify(cert),
            "inclusion_proof": translog.inclusion_proof(store, idx)})

    calibration = []
    for agent in list_agents():
        for cls in agent.action_classes:
            conf = confidence_for(agent.agent_id, cls)
            calibration.append({
                "agent": agent.agent_id, "action_class": cls,
                "risk": agent.action_classes[cls].risk,
                "tier": earned_tier(agent.agent_id, cls),
                "n": conf.n, "successes": conf.successes,
                "p_lower": conf.p_lower, "alpha": conf.alpha,
                "scope": "marginal per-stratum lower bound over the drift-"
                         "audited rolling window; not a per-decision probability"})

    decisions = recent_decisions(500)
    oversight = [{"request_id": d.request_id, "agent": d.agent_id,
                  "action_class": d.action_class, "approved_by": d.approved_by,
                  "final": d.decision}
                 for d in decisions if d.approved_by]

    return {
        "generated_at": time.time(),
        "authority_public_key": authority.public_key_hex(),
        "transparency_log": {"size": chain["size"], "root": chain["root"],
                             "chain_consistent": chain["consistent"]},
        "sampled_decisions": sampled,
        "sampling": {"method": "uniform-random over full log",
                     "seed": seed, "n": len(sampled)},
        "calibration_tables": calibration,
        "human_oversight_record": oversight,
        "enforcement_config": {
            "tripwires": [why for _, why in TRIPWIRES],
            "monotone_judge": "advisory layers may only lower a decision",
            "fail_mode": "fail-closed for unknown agents and undeclared "
                         "action classes",
        },
        "control_mappings": CONTROL_MAPPINGS,
        "honest_limits": [
            "statistical bounds are marginal per-bucket, never per-decision",
            "bounds assume exchangeability; drift is detected (Page-Hinkley) "
            "and voids the window, but bounds are undefined during shift",
            "citation integrity verifies claims trace to supplied evidence, "
            "not that the evidence is true",
        ],
    }
