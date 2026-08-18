"""Counterfactual policy replay: test a policy change against history first.

Every team tightening or loosening an AI-agent policy today does it blind —
change the config, watch what breaks, apologise. Replay answers the question
that should precede every change: "had this policy been in force, what would
KEEL have decided differently, over the actions my agents ACTUALLY took?"

It re-runs the recorded decision evidence — the stored check verdicts,
calibration state, and earned tier at decision time — through the decision
policy under hypothetical knobs, with no side effects: nothing is stored, no
certificate is issued, no calibration moves. The output is a diff: which past
decisions flip, in which direction, and why.

Honest limits, stated in every report:
  - Replay is EVIDENCE-preserving, not behaviour-preserving. It cannot know
    what an agent would have done differently had it been blocked earlier.
  - Checker verdicts are replayed as recorded. A knob that would have changed
    a check's own input (a different budget changing spend history) is beyond
    replay's reach.
  - The advisory layers (conformal risk control, the RL bandit) are not
    replayed — they adapt online, so their historical state under a different
    policy is unknowable. Both only ever tighten, so replay's ALLOW counts are
    an UPPER bound: the real policy would allow the same or less.
"""
from __future__ import annotations

from typing import Any, Optional

from .engine import (MIN_OUTCOMES, P_LOWER_REQ, TIER_REQ, agent_owner,
                     passport_prior, recent_decisions)
from .models import GatewayDecision

# knobs a caller may turn; anything else in `changes` is rejected loudly
KNOBS = ("risk_overrides", "floor_delta", "tier_req", "enforce_shadow")


def _would_decide(d: GatewayDecision, risk: str, floor: float,
                  needed_tier: int, enforce_shadow: bool) -> tuple[str, str]:
    """The pure core of decide(), over one recorded decision's evidence."""
    # Shadow FIRST: decide() records SHADOW even when checks hard-fail (that
    # is what shadow mode is). Testing hard_fails before this branch would
    # fabricate SHADOW→BLOCK flips under an identity policy.
    if d.decision == "SHADOW" and not enforce_shadow:
        return "SHADOW", "agent in shadow mode (set enforce_shadow to preview exit)"
    hard_fails = [c for c in d.checks if c.verdict == "fail"]
    warns = [c for c in d.checks if c.verdict == "warn"]
    if hard_fails:
        return "BLOCK", f"recorded check failed: {hard_fails[0].checker}"

    conf = d.confidence
    if not conf.sufficient:
        if risk == "low":
            return "ALLOW", "cold start permitted at low risk"
        if risk == "medium":
            # decide() bridges medium-risk cold start with a verified passport
            # prior; mirror its gates exactly — the tightened floor semantics
            # (here: the candidate policy's floor), the local-evidence veto,
            # and the destructive-intent gate — or replay would fabricate or
            # hide flips. Consulted at replay time (CURRENT adoption state),
            # which the report's limits note.
            pp = passport_prior(d.agent_id, d.action_class)
            bridge_ok = (pp is not None and pp["p_lower"] >= floor
                         and conf.n < MIN_OUTCOMES
                         and conf.successes == conf.n
                         and "post-drift" not in (conf.stratum or ""))
            if bridge_ok:
                if any(c.checker == "destructive_intent" for c in warns):
                    return "ESCALATE", ("destructive action requires review "
                                        "even with a passport")
                return "ALLOW", "cold start bridged by verified agent passport"
        return "ESCALATE", f"insufficient calibration (n={conf.n}<{MIN_OUTCOMES})"
    if (conf.p_lower or 0.0) < floor:
        return "ESCALATE", (f"calibrated floor {conf.p_lower:.2f} below "
                            f"required {floor:.2f}")
    if d.tier < needed_tier:
        return "ESCALATE", f"earned tier T{d.tier} below required T{needed_tier}"
    if any(c.checker == "destructive_intent" for c in warns) and risk != "low":
        return "ESCALATE", "novel destructive action requires review"
    if warns and risk in ("high", "critical"):
        return "ESCALATE", f"advisory warning at {risk} risk: {warns[0].checker}"
    return "ALLOW", "all recorded checks pass under the candidate policy"


def replay(changes: dict[str, Any], account_id: Optional[str] = None,
           limit: int = 500) -> dict[str, Any]:
    """Diff the candidate policy against recorded decisions.

    `account_id` scopes to one tenant's agents (None = whole deployment, for
    self-host). Unknown knobs are an error rather than a silent no-op — a
    typo'd knob that "worked" would green-light an untested policy.
    """
    unknown = set(changes) - set(KNOBS)
    if unknown:
        return {"error": f"unknown policy knobs: {sorted(unknown)}",
                "known": list(KNOBS)}
    risk_overrides: dict[str, str] = changes.get("risk_overrides") or {}
    bad_risk = {v for v in risk_overrides.values()
                if v not in ("low", "medium", "high", "critical")}
    if bad_risk:
        return {"error": f"invalid risk levels: {sorted(bad_risk)}"}
    floor_delta = float(changes.get("floor_delta") or 0.0)
    tier_req = {**TIER_REQ, **(changes.get("tier_req") or {})}
    enforce_shadow = bool(changes.get("enforce_shadow"))

    # filter to the tenant BEFORE applying the limit: slicing the global
    # newest-N first would let other tenants' traffic crowd this account's
    # decisions out of its own replay
    decisions = recent_decisions(10_000)
    if account_id is not None:
        decisions = [d for d in decisions
                     if agent_owner(d.agent_id) == account_id]
    decisions = decisions[:limit]                  # newest-first per tenant

    diffs, matrix = [], {}
    for d in decisions:
        risk = risk_overrides.get(d.action_class, d.risk)
        floor = min(0.99, P_LOWER_REQ[risk] + floor_delta)
        was = d.decision
        # a recorded SHADOW's counterfactual baseline is what it recorded it
        # would have done; under enforce_shadow we recompute it as enforcing
        would, why = _would_decide(d, risk, floor, tier_req[risk], enforce_shadow)
        matrix[f"{was}→{would}"] = matrix.get(f"{was}→{would}", 0) + 1
        if would != was:
            diffs.append({"request_id": d.request_id, "agent": d.agent_id,
                          "action_class": d.action_class, "was": was,
                          "would_be": would, "why": why})

    newly_blocked = sum(1 for x in diffs
                        if x["would_be"] in ("BLOCK", "ESCALATE")
                        and x["was"] == "ALLOW")
    newly_allowed = sum(1 for x in diffs if x["would_be"] == "ALLOW"
                        and x["was"] in ("BLOCK", "ESCALATE"))
    return {
        "replayed": len(decisions),
        "changes": {"risk_overrides": risk_overrides, "floor_delta": floor_delta,
                    "tier_req": tier_req, "enforce_shadow": enforce_shadow},
        "transitions": dict(sorted(matrix.items())),
        "flips": diffs,
        "summary": {
            "unchanged": len(decisions) - len(diffs),
            "would_tighten": newly_blocked,
            "would_loosen": newly_allowed,
        },
        "honest_limits": [
            "evidence-preserving, not behaviour-preserving: agents would have "
            "acted differently under the candidate policy",
            "passport priors are consulted at replay time (current adoption "
            "state), not as of each historical decision",
            "checker verdicts replayed as recorded; knobs that would change a "
            "checker's own inputs are out of scope",
            "adaptive advisory layers (risk control, RL bandit) are not "
            "replayed; both only tighten, so ALLOW counts are an upper bound",
        ],
    }
