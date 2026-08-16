"""Gateway engine: registration, decision, calibration, approvals, audit.

Autonomy is EARNED per (agent, action-class) from recorded outcomes — the same
retention mechanic as KEEL's ops verticals, generalized. Every decision is an
Ed25519-signed certificate in the gateway's own Merkle transparency log, which
is what makes an agent's action history admissible in a postmortem, an audit,
or an EU-AI-Act record-keeping review.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from ..cert import authority
from ..models import Certificate
from ..store import Store, get_store
from .adaptive import adaptive_window, anomaly_score, observe_transition
from .advanced import (record_allowed_outcome, risk_control_state,
                       wsr_lower_bound)
from .checkers import (check_consistency, check_destructive_intent,
                       check_grounding, check_policy, check_schema,
                       check_tripwires, clopper_pearson_lower, llm_judge)
from .models import (ActionClassSpec, ActionOutcome, ActionRequest,
                     AgentProfile, CheckResult, Confidence, GatewayDecision)

MIN_OUTCOMES = 5                      # below this there is no calibration, only caution
P_LOWER_REQ = {"low": 0.50, "medium": 0.70, "high": 0.85, "critical": 0.95}
TIER_REQ = {"low": 1, "medium": 1, "high": 2, "critical": 3}

_AGENTS = "gw_agents"
_DECISIONS = "gw_decisions"
_STRATA = "gw_strata"                 # "agent|class" -> {n, successes, harms, last[]}
_IDEMP = "gw_idempotency"
_SPEND = "gw_spend"


def gw_store() -> Store:
    return get_store("gateway")


# ── registration ─────────────────────────────────────────────────────────────

def register_agent(profile: AgentProfile) -> AgentProfile:
    store = gw_store()
    agents = store.kv_get(_AGENTS, {})
    agents[profile.agent_id] = profile.model_dump(by_alias=True)
    store.kv_set(_AGENTS, agents)
    return profile


def get_agent(agent_id: str) -> Optional[AgentProfile]:
    raw = gw_store().kv_get(_AGENTS, {}).get(agent_id)
    return AgentProfile.model_validate(raw) if raw else None


def list_agents() -> list[AgentProfile]:
    return [AgentProfile.model_validate(a)
            for a in gw_store().kv_get(_AGENTS, {}).values()]


# ── calibration (per agent + action class) ───────────────────────────────────

def _stratum_key(agent_id: str, action_class: str) -> str:
    return f"{agent_id}|{action_class}"


def confidence_for(agent_id: str, action_class: str,
                   alpha: float = 0.10) -> Confidence:
    s = gw_store().kv_get(_STRATA, {}).get(_stratum_key(agent_id, action_class))
    if not s:
        return Confidence(stratum=_stratum_key(agent_id, action_class), alpha=alpha)
    window, drifted = adaptive_window(s.get("last", []))
    n = len(window)
    succ = sum(1 for o in window if o.get("success"))
    stratum = _stratum_key(agent_id, action_class)
    if drifted:
        stratum += "|post-drift"
    stream = [int(bool(o.get("success"))) for o in window]
    anytime = round(wsr_lower_bound(stream, alpha), 4) if n else None
    cp = round(clopper_pearson_lower(succ, n, alpha), 4) if n else None
    return Confidence(
        n=n, successes=succ,
        p_hat=round(succ / n, 4) if n else None,
        # the gate consults this continuously → the anytime-valid bound is
        # the honest one; CP (fixed-n) is reported inside the certificate
        p_lower=anytime if anytime is not None else cp,
        alpha=alpha, stratum=stratum + f"|cp={cp}",
        sufficient=n >= MIN_OUTCOMES and not (drifted and n < 2 * MIN_OUTCOMES))


def earned_tier(agent_id: str, action_class: str) -> int:
    """T1 by default; T2/T3 earned from this stratum's own outcomes."""
    s = gw_store().kv_get(_STRATA, {}).get(_stratum_key(agent_id, action_class))
    if not s:
        return 1
    window, drifted = adaptive_window(s.get("last", []))
    n = len(window)
    succ = sum(1 for o in window if o.get("success"))
    harms = int(s.get("harms", 0))
    n_ext = int(s.get("n_external", 0))
    if drifted:
        return 1          # regime change resets autonomy until re-earned
    p_lo = clopper_pearson_lower(succ, n) if n else 0.0
    # tier upgrades require externally-verified outcomes: an agent's own
    # success reports never promote it (Replit falsely reported rollback state)
    if n >= 30 and n_ext >= 15 and p_lo >= 0.95 and harms == 0:
        return 3
    if n >= 10 and n_ext >= 5 and p_lo >= 0.80 and harms == 0:
        return 2
    return 1


# ── the decision ─────────────────────────────────────────────────────────────

def decide(req: ActionRequest) -> GatewayDecision:
    store = gw_store()
    agent = get_agent(req.agent_id)
    if agent is None:
        return _finalize(store, req, "BLOCK", "critical", 0, [],
                         ["unknown agent — register before submitting actions"],
                         Confidence())
    spec = agent.action_classes.get(req.action_class)
    if spec is None:
        return _finalize(store, req, "BLOCK", "critical", 0, [],
                         [f"action class '{req.action_class}' was never declared "
                          "by this agent — undeclared capabilities are refused"],
                         Confidence())

    checks: list[CheckResult] = []
    trip = check_tripwires(req)
    checks.append(trip)
    if trip.verdict == "fail":
        # the absolute floor: enforced in EVERY mode, shadow included
        return _finalize(store, req, "BLOCK", spec.risk,
                         earned_tier(req.agent_id, req.action_class), checks,
                         [f"tripwire: {trip.detail} — hard-blocked in all modes"],
                         confidence_for(req.agent_id, req.action_class))
    spent, hourly = _spend_stats(store, req.agent_id, req.action_class)
    seen_idem = bool(req.idempotency_key) and \
        req.idempotency_key in store.kv_get(_IDEMP, {})
    checks.append(check_schema(req, spec))
    checks.append(check_policy(req, spec, spent, hourly))
    checks.append(check_grounding(req, spec))
    checks.append(check_consistency(req, spec, seen_idem))
    destr = check_destructive_intent(req)
    if destr.verdict == "warn":
        checks.append(destr)
    excess, anom_detail = anomaly_score(store, req.agent_id, req.action_class)
    if excess >= 3.0:
        checks.append(CheckResult(checker="behavioral_anomaly", verdict="warn",
                                  detail=f"unusual for this agent: {anom_detail}"))
    observe_transition(store, req.agent_id, req.action_class)
    if spec.risk in ("high", "critical"):        # latency contract: judge only
        judge = llm_judge(req)                    # where escalation is likely
        if judge is not None:
            checks.append(judge)

    conf = confidence_for(req.agent_id, req.action_class)
    tier = earned_tier(req.agent_id, req.action_class)
    risk = spec.risk
    reasons: list[str] = []

    hard_fails = [c for c in checks if c.verdict == "fail"]
    warns = [c for c in checks if c.verdict == "warn"]

    if agent.shadow_mode:
        decision = "SHADOW"
        reasons.append("agent is in shadow mode — decision recorded, nothing enforced")
        if hard_fails:
            reasons += [f"would BLOCK: {c.checker} — {c.detail}" for c in hard_fails]
    elif hard_fails:
        decision = "BLOCK"
        reasons += [f"{c.checker}: {c.detail}" for c in hard_fails]
    else:
        crc = risk_control_state(store, risk)
        needed_p = min(0.99, P_LOWER_REQ[risk] + crc["tighten"])
        needed_tier = TIER_REQ[risk]
        if crc["status"] == "TIGHTENED":
            reasons.append(f"risk control: harm rate UCB {crc['harm_ucb']} "
                           f"exceeds budget {crc['harm_budget']} for {risk}-risk "
                           f"ALLOWs — floor tightened to {needed_p:.2f}")
        if not conf.sufficient:
            if risk == "low":
                decision = "ALLOW"
                reasons.append(f"cold start (n={conf.n}<{MIN_OUTCOMES}) permitted "
                               "for low-risk class; outcomes will calibrate it")
            else:
                decision = "ESCALATE"
                reasons.append(f"no calibration yet for this agent+class "
                               f"(n={conf.n}<{MIN_OUTCOMES}); {risk}-risk actions "
                               "require a human until the track record exists")
        elif (conf.p_lower or 0.0) < needed_p:
            decision = "ESCALATE"
            reasons.append(f"calibrated success floor {conf.p_lower:.2f} below the "
                           f"{needed_p:.2f} required for {risk} risk "
                           f"(n={conf.n}, {conf.successes} successes)")
        elif tier < needed_tier:
            decision = "ESCALATE"
            reasons.append(f"{risk}-risk auto-execution requires earned tier "
                           f"T{needed_tier}; this agent+class has earned T{tier}")
        elif any(c.checker == "destructive_intent" for c in warns) and risk != "low":
            decision = "ESCALATE"
            reasons.append("novel destructive action requires human review even "
                           "at earned trust: " +
                           next(c.detail for c in warns
                                if c.checker == "destructive_intent"))
        elif warns and risk in ("high", "critical"):
            decision = "ESCALATE"
            reasons += [f"advisory at {risk} risk: {c.checker} — {c.detail}"
                        for c in warns]
        else:
            decision = "ALLOW"
            reasons.append(f"all checks pass · calibrated floor "
                           f"{conf.p_lower:.2f} ≥ {needed_p:.2f} · tier T{tier}")
            if warns:
                reasons += [f"note: {c.checker} — {c.detail}" for c in warns]

    if req.idempotency_key:
        idem = store.kv_get(_IDEMP, {})
        idem[req.idempotency_key] = req.request_id
        store.kv_set(_IDEMP, dict(list(idem.items())[-800:]))
    if decision == "ALLOW":
        _record_spend(store, req)
    return _finalize(store, req, decision, risk, tier, checks, reasons, conf)


def _finalize(store: Store, req: ActionRequest, decision: str, risk: str,
              tier: int, checks: list[CheckResult], reasons: list[str],
              conf: Confidence) -> GatewayDecision:
    dec = GatewayDecision(
        request_id=req.request_id, agent_id=req.agent_id,
        action_class=req.action_class, decision=decision, reasons=reasons,
        checks=checks, confidence=conf, risk=risk, tier=tier)
    cert = Certificate(
        cert_id=authority.new_cert_id(), tenant=f"agent:{req.agent_id}",
        incident_id=req.request_id,
        claim={"root_cause": f"{req.agent_id}|{req.action_class}",
               "mechanism": req.intent[:300], "generator": "keel-gateway"},
        claimant=req.agent_id,
        verdict={"ALLOW": "SUPPORTED", "BLOCK": "REFUTED",
                 "ESCALATE": "AMBIGUOUS", "ABSTAIN": "ABSTAIN",
                 "SHADOW": "INSUFFICIENT"}[decision],
        evidence_summary={"targets": req.targets[:10], "cost": req.cost,
                          "claims": len(req.claims), "evidence": len(req.evidence)},
        refutation=[c.model_dump() for c in checks],
        conformal={"n": conf.n, "p_lower": conf.p_lower, "alpha": conf.alpha,
                   "strata": conf.stratum, "sufficient": conf.sufficient,
                   "scope": "anytime-valid betting-martingale lower bound "
                            "(valid under continuous monitoring, Ville) over "
                            "the drift-audited window; Clopper-Pearson in "
                            "stratum tag for reference; marginal per-stratum; "
                            "NOT a per-action probability; void under shift"},
        gate={"decision": decision, "risk": risk, "tier": tier,
              "reasons": reasons[:8]},
        autonomy_tier=tier,
        decision=f"{decision} — " + (reasons[0] if reasons else ""),
        graph_version="gateway", scm_version="gateway-v1",
        model_version="keel-0.3.0", created_at=time.time())
    cert = authority.issue(store, cert)
    dec.cert_id = cert.cert_id
    decs = store.kv_get(_DECISIONS, {})
    decs[req.request_id] = dec.model_dump()
    store.kv_set(_DECISIONS, dict(list(decs.items())[-600:]))
    return dec


# ── approvals & outcomes (the closing loop) ──────────────────────────────────

def get_decision(request_id: str) -> Optional[GatewayDecision]:
    raw = gw_store().kv_get(_DECISIONS, {}).get(request_id)
    return GatewayDecision.model_validate(raw) if raw else None


def pending_approvals() -> list[GatewayDecision]:
    return [d for d in (GatewayDecision.model_validate(x)
                        for x in gw_store().kv_get(_DECISIONS, {}).values())
            if d.decision == "ESCALATE" and not d.approved_by]


def approve(request_id: str, approver: str, allow: bool,
            note: str = "") -> Optional[GatewayDecision]:
    store = gw_store()
    dec = get_decision(request_id)
    if dec is None or dec.decision != "ESCALATE":
        return None
    dec.decision = "ALLOW" if allow else "BLOCK"
    dec.approved_by = approver
    dec.reasons.append(f"human {'approval' if allow else 'denial'} by {approver}"
                       + (f": {note}" if note else ""))
    release = Certificate(
        cert_id=authority.new_cert_id(), tenant=f"agent:{dec.agent_id}",
        incident_id=request_id,
        claim={"root_cause": f"{dec.agent_id}|{dec.action_class}",
               "mechanism": f"human oversight release of {dec.cert_id}",
               "generator": "keel-gateway"},
        claimant=approver,
        verdict="SUPPORTED" if allow else "REFUTED",
        gate={"decision": dec.decision, "released_from": dec.cert_id,
              "note": note},
        decision=f"HUMAN-{'APPROVED' if allow else 'DENIED'} by {approver}",
        graph_version="gateway", scm_version="gateway-v1",
        model_version="keel-0.3.0", created_at=time.time())
    authority.issue(store, release)
    decs = store.kv_get(_DECISIONS, {})
    decs[request_id] = dec.model_dump()
    store.kv_set(_DECISIONS, decs)
    return dec


def record_outcome(outcome: ActionOutcome) -> dict[str, Any]:
    store = gw_store()
    dec = get_decision(outcome.request_id)
    if dec is None:
        return {"error": "unknown request_id"}
    dec.executed = True
    dec.outcome = "success" if outcome.success else "failure"
    decs = store.kv_get(_DECISIONS, {})
    decs[outcome.request_id] = dec.model_dump()
    store.kv_set(_DECISIONS, decs)

    if dec.decision == "ALLOW":
        record_allowed_outcome(store, dec.risk, outcome.harm)
    strata = store.kv_get(_STRATA, {})
    key = _stratum_key(dec.agent_id, dec.action_class)
    s = strata.get(key, {"n": 0, "successes": 0, "harms": 0, "last": []})
    s["n"] += 1
    s["successes"] += int(outcome.success)
    s["harms"] += int(outcome.harm)
    if outcome.reported_by not in ("agent", "self", ""):
        s["n_external"] = int(s.get("n_external", 0)) + 1
    s["last"] = (s.get("last", []) + [{
        "ts": outcome.ts, "success": outcome.success, "harm": outcome.harm}])[-50:]
    strata[key] = s
    store.kv_set(_STRATA, strata)
    conf = confidence_for(dec.agent_id, dec.action_class)
    return {"recorded": True, "stratum": key,
            "confidence": conf.model_dump(),
            "tier": earned_tier(dec.agent_id, dec.action_class)}


def recent_decisions(limit: int = 60) -> list[GatewayDecision]:
    decs = [GatewayDecision.model_validate(x)
            for x in gw_store().kv_get(_DECISIONS, {}).values()]
    return sorted(decs, key=lambda d: -d.created_at)[:limit]


# ── spend / rate accounting ──────────────────────────────────────────────────

def _spend_stats(store: Store, agent_id: str, action_class: str
                 ) -> tuple[float, int]:
    sp = store.kv_get(_SPEND, {})
    day = time.strftime("%Y-%m-%d")
    hour = int(time.time() // 3600)
    key_d = f"{agent_id}|{action_class}|{day}"
    key_h = f"{agent_id}|{action_class}|h{hour}"
    return float(sp.get(key_d, 0.0)), int(sp.get(key_h, 0))


def _record_spend(store: Store, req: ActionRequest) -> None:
    sp = store.kv_get(_SPEND, {})
    day = time.strftime("%Y-%m-%d")
    hour = int(time.time() // 3600)
    key_d = f"{req.agent_id}|{req.action_class}|{day}"
    key_h = f"{req.agent_id}|{req.action_class}|h{hour}"
    sp[key_d] = float(sp.get(key_d, 0.0)) + req.cost
    sp[key_h] = int(sp.get(key_h, 0)) + 1
    store.kv_set(_SPEND, dict(list(sp.items())[-1000:]))
