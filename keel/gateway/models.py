"""Gateway contracts. These are universal on purpose: an action is anything an
agent wants to do; a claim is anything it asserts; evidence is whatever it
grounds those claims in. No field-specific vocabulary anywhere."""
from __future__ import annotations

import time
import uuid
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

RiskClass = Literal["low", "medium", "high", "critical"]


class ActionClassSpec(BaseModel):
    """What an agent is allowed to attempt, declared at registration."""
    name: str                                   # e.g. 'send_email', 'issue_refund'
    risk: RiskClass = "medium"
    schema_: Optional[dict[str, Any]] = Field(default=None, alias="schema")
    requires_reversible: bool = False
    requires_evidence: bool = False             # claims must be grounded
    budget_per_day: Optional[float] = None      # sum of action `cost`
    max_per_hour: Optional[int] = None
    protected_targets: list[str] = Field(default_factory=list)  # never touch
    model_config = {"populate_by_name": True}


class AgentProfile(BaseModel):
    agent_id: str
    name: str
    owner: str = ""
    framework: str = ""                         # langchain / openai / crewai / custom
    action_classes: dict[str, ActionClassSpec] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    owner_account: str = ""                     # account that owns this agent
    shadow_mode: bool = True                    # SHADOW-FIRST: observe, sign,
                                                # flag would-have-blocked; only
                                                # tripwires enforce on day one


class Claim(BaseModel):
    statement: str
    evidence_refs: list[str] = Field(default_factory=list)  # ids into evidence


class EvidenceItem(BaseModel):
    ref: str
    content: str
    source: str = ""
    ts: Optional[float] = None


class ActionRequest(BaseModel):
    request_id: str = Field(default_factory=lambda: f"req-{uuid.uuid4().hex[:12]}")
    agent_id: str
    action_class: str
    intent: str = ""                            # human-readable what/why
    payload: dict[str, Any] = Field(default_factory=dict)
    targets: list[str] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    cost: float = 0.0                           # domain units (currency, blast size…)
    reversible: bool = True
    rollback_plan: str = ""
    idempotency_key: str = ""
    context: dict[str, Any] = Field(default_factory=dict)
    ts: float = Field(default_factory=time.time)


class CheckResult(BaseModel):
    checker: str
    verdict: Literal["pass", "fail", "warn", "abstain"]
    score: Optional[float] = None
    detail: str = ""


class Confidence(BaseModel):
    """Distribution-free lower confidence bound on success for this
    agent + action class, from ITS OWN recorded outcomes. Never a vibe."""
    n: int = 0
    successes: int = 0
    p_hat: Optional[float] = None
    p_lower: Optional[float] = None             # Clopper-Pearson lower bound
    alpha: float = 0.10
    stratum: str = ""
    sufficient: bool = False                    # n >= MIN_OUTCOMES


class GatewayDecision(BaseModel):
    request_id: str
    agent_id: str
    action_class: str
    decision: Literal["ALLOW", "BLOCK", "ESCALATE", "ABSTAIN", "SHADOW"]
    reasons: list[str] = Field(default_factory=list)
    checks: list[CheckResult] = Field(default_factory=list)
    confidence: Confidence = Field(default_factory=Confidence)
    risk: RiskClass = "medium"
    tier: int = 0                               # earned autonomy tier at decision time
    cert_id: str = ""
    created_at: float = Field(default_factory=time.time)
    approved_by: str = ""                       # set when a human releases an ESCALATE
    executed: Optional[bool] = None
    outcome: Optional[str] = None               # 'success' | 'failure' | …


class ActionOutcome(BaseModel):
    request_id: str
    success: bool
    detail: str = ""
    cost_actual: Optional[float] = None
    harm: bool = False                          # did executing cause damage
    reported_by: str = "agent"
    ts: float = Field(default_factory=time.time)
