"""Policy decision point: organizational authorization, separate from safety.

Rego-flavoured declarative rules evaluated over the certificate draft.
Default deny. The autonomy tier a tenant may exercise is *earned* from its own
accumulated outcomes — corpus size, executed actions, success rate — which is
the retention mechanic: the longer KEEL runs, the higher the tier unlocked.
"""
from __future__ import annotations

import time
from typing import Any

from ..config import (AUTONOMY_TIERS, CONFORMAL_ALPHA, in_change_window)
from ..calibrate.conformal import corpus
from ..store import Store

OVERRIDES_KEY = "policy_overrides"


def tenant_autonomy(store: Store) -> dict[str, Any]:
    """Which tier has this tenant earned from its own outcome history?"""
    n_corpus = len(corpus(store))
    outs = store.outcomes()
    executed = [o for o in outs if o.action_executed]
    successes = [o for o in executed if o.action_outcome == "resolved"]
    rate = len(successes) / len(executed) if executed else 0.0

    tier = 0
    if n_corpus >= 25:
        tier = 1
    if n_corpus >= 60 and len(successes) >= 3 and rate >= 0.80:
        tier = 2
    if n_corpus >= 120 and len(successes) >= 8 and rate >= 0.95:
        tier = 3
    ov = store.kv_get(OVERRIDES_KEY, {})
    if "max_tier" in ov:
        tier = min(tier, int(ov["max_tier"]))
    return {"tier": tier, "corpus_n": n_corpus, "executed": len(executed),
            "successes": len(successes), "success_rate": round(rate, 3),
            "next_unlock": _next_unlock(n_corpus, len(successes), rate)}


def _next_unlock(n: int, succ: int, rate: float) -> str:
    if n < 25:
        return f"T1 at 25 calibration examples (have {n})"
    if n < 60 or succ < 3 or rate < 0.80:
        return f"T2 at 60 examples + 3 successes @80% (have {n}, {succ})"
    if n < 120 or succ < 8 or rate < 0.95:
        return f"T3 at 120 examples + 8 successes @95% (have {n}, {succ})"
    return "maximum tier unlocked"


def decide(store: Store, cert_draft: dict[str, Any],
           action_class: str, when: float | None = None) -> dict[str, Any]:
    """The PDP. Returns {allow, escalate, tier, reasons[]}."""
    auto = tenant_autonomy(store)
    tier_n = auto["tier"]
    tier = AUTONOMY_TIERS[tier_n]
    reasons: list[str] = []
    hour = time.localtime(when or time.time()).tm_hour

    verdict = cert_draft.get("verdict")
    pn_lo = cert_draft.get("pn_lo") or 0.0
    blast = cert_draft.get("blast_radius") or {}
    twin = cert_draft.get("twin") or {}
    drift_level = (cert_draft.get("drift") or {}).get("level", "ok")

    prior = _prior_successes(store, action_class)

    checks = [
        (verdict == "SUPPORTED", f"verdict is {verdict}, requires SUPPORTED"),
        (pn_lo >= tier.pn_lower_min,
         f"PN lower bound {pn_lo:.2f} below tier minimum {tier.pn_lower_min:.2f}"),
        (cert_draft.get("conformal", {}).get("alpha", 1.0) <= CONFORMAL_ALPHA + 1e-9,
         "conformal alpha above policy maximum 0.10"),
        (blast.get("slas_at_risk", 0) <= tier.max_slas_at_risk,
         f"{blast.get('slas_at_risk', 0)} SLAs at risk exceeds tier allowance"),
        (len(blast.get("elements", [])) <= tier.max_blast_elements,
         f"blast radius {len(blast.get('elements', []))} elements exceeds tier "
         f"allowance {tier.max_blast_elements}"),
        ((not tier.requires_reversible) or twin.get("rollback_verified", False),
         "tier requires verified reversibility"),
        (drift_level != "breach", "drift gate in breach — certification frozen"),
        (in_change_window(hour), f"outside change window (hour={hour:02d})"),
        (prior >= tier.min_prior_successes,
         f"{prior} prior successes of '{action_class}', tier requires "
         f"{tier.min_prior_successes}"),
    ]
    failed = [msg for ok, msg in checks if not ok]
    allow = not failed and tier_n >= 2
    escalate = (verdict == "SUPPORTED" and bool(failed))
    if not failed:
        reasons.append(f"all {len(checks)} policy checks satisfied at {tier.name}")
    else:
        reasons.extend(failed)
    return {"allow": allow, "escalate": escalate, "tier": tier_n,
            "tier_name": tier.name, "reasons": reasons,
            "autonomy": auto, "prior_successes": prior,
            "change_window_ok": in_change_window(hour)}


def _prior_successes(store: Store, action_class: str) -> int:
    n = 0
    for o in store.outcomes():
        if o.action_executed and o.action_outcome == "resolved":
            cert = store.certificate(o.cert_id)
            if cert and cert.action and cert.action.get("action_class") == action_class:
                n += 1
    return n
