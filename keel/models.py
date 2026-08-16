"""Domain models. These are the contracts between planes.

The hard schema boundary of the whole system lives here: a hypothesis that
does not validate as `Hypothesis` never reaches adjudication, and a
certificate is always a `Certificate` — the signed JSON the product exists
to produce.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# ── Substrate ────────────────────────────────────────────────────────────────


class Entity(BaseModel):
    entity_id: str
    kind: str      # 'ne' | 'link' | 'service' | 'site' | 'power' (shared infra)
    layer: str     # domain-pack layer vocabulary, e.g. 'optical', 'db', 'scada' 
    vendor: str = ""
    model: str = ""
    site: str = ""
    attrs: dict[str, Any] = Field(default_factory=dict)


class TopoEdge(BaseModel):
    """Directed dependency: failure at src can propagate to dst."""

    src: str
    dst: str
    relation: str                       # 'carries', 'feeds', 'peers', 'serves'
    valid_from: float
    valid_to: Optional[float] = None    # None = current (bi-temporal)


class Event(BaseModel):
    event_id: int = 0
    incident_id: Optional[str] = None
    entity_id: str
    event_type: str                     # the Hawkes mark, e.g. 'optical.los'
    severity: int = 3                   # 1=critical … 5=info
    ts: float                           # epoch seconds
    raw: dict[str, Any] = Field(default_factory=dict)
    suppressed: bool = False            # true if explained by upstream intensity
    info_gain: float = 1.0              # bits of new information carried


class Incident(BaseModel):
    incident_id: str
    title: str
    scenario: str                       # generator scenario key ('' if external)
    severity: Literal["P1", "P2", "P3"] = "P1"
    t0: float
    t1: float
    status: Literal["open", "verifying", "certified", "resolved", "abstained"] = "open"
    ground_truth: Optional[str] = None  # root-cause variable; None until resolved
    entities: list[str] = Field(default_factory=list)
    alarm_count: int = 0
    sla_services: list[str] = Field(default_factory=list)


# ── Hypothesis (P3) ──────────────────────────────────────────────────────────


class Intervention(BaseModel):
    variable: str = Field(description="canonical '<entity_id>|<event_type>' variable")
    set_to: str = Field(default="nominal", description="counterfactual value")


class Hypothesis(BaseModel):
    hypothesis_id: str
    intervention: Intervention
    mechanism: str
    predicted_path: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    prior_confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    source: str = "keel-hypothesizer"   # which agent proposed it


# ── Adjudication (P4) ────────────────────────────────────────────────────────


class RefutationResult(BaseModel):
    refuter: str
    passed: bool
    detail: str
    delta: float = 0.0


class Adjudicated(BaseModel):
    hypothesis: Hypothesis
    pn: Optional[float] = None          # None when only bounds are identified
    pn_lo: float = 0.0
    pn_hi: float = 1.0
    ps: Optional[float] = None
    ps_lo: float = 0.0
    ps_hi: float = 1.0
    point_identified: bool = False
    identification: str = ""            # 'backdoor' | 'bounds:tian-pearl' | …
    refutations: list[RefutationResult] = Field(default_factory=list)
    refutation_passed: bool = True
    score: float = 0.0                  # normalized ranking score


# ── Calibration (P5) ─────────────────────────────────────────────────────────


class DriftReport(BaseModel):
    energy_distance: float = 0.0
    graph_edit_distance: float = 0.0
    fidelity_residual: float = 0.0
    level: Literal["ok", "widened", "breach"] = "ok"
    notes: list[str] = Field(default_factory=list)


class CalibrationResult(BaseModel):
    conformal_set: list[str]            # hypothesis_ids inside the set
    alpha: float
    q_hat: Optional[float] = None
    calibration_n: int = 0
    strata: str = "marginal"
    drift: DriftReport = Field(default_factory=DriftReport)
    abstain_reason: Optional[str] = None


# ── Actuation (P6) ───────────────────────────────────────────────────────────


class RemediationAction(BaseModel):
    action_id: str
    action_class: str                   # 'reroute', 'drain', 'restart', 'rollback_config'
    description: str
    target_entities: list[str]
    parameters: dict[str, Any] = Field(default_factory=dict)
    reversible: bool = True
    rollback_plan: str = ""


class BlastRadius(BaseModel):
    elements: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    slas_at_risk: int = 0
    customers_affected: int = 0


class TwinPrediction(BaseModel):
    tier: int = 1
    resolves_incident: bool = False
    p_resolve: float = 0.0
    restore_minutes: float = 0.0
    restore_lo: float = 0.0
    restore_hi: float = 0.0
    additional_impact: list[str] = Field(default_factory=list)
    rollback_verified: bool = False
    rollback_seconds: float = 0.0
    fidelity_score: float = 1.0         # measured, per action class
    trajectory: list[dict[str, Any]] = Field(default_factory=list)


class GateDecision(BaseModel):
    decision: Literal["SIGN", "BLOCK", "ESCALATE"]
    reason: str = ""
    constraints: dict[str, Any] = Field(default_factory=dict)
    violated: list[str] = Field(default_factory=list)
    projected: bool = False             # true if shield substituted a safer variant
    policy: dict[str, Any] = Field(default_factory=dict)
    tier_authorized: int = 0


# ── Certificate (the product) ────────────────────────────────────────────────


class Certificate(BaseModel):
    cert_id: str
    schema_version: str = "keel-certificate/v1"
    tenant: str = ""
    incident_id: str
    claim: dict[str, Any]
    claimant: str = "keel-hypothesizer"
    verdict: Literal["SUPPORTED", "REFUTED", "AMBIGUOUS", "ABSTAIN", "INSUFFICIENT"]
    pn: Optional[float] = None
    pn_lo: float = 0.0
    pn_hi: float = 1.0
    ps: Optional[float] = None
    ps_lo: float = 0.0
    ps_hi: float = 1.0
    point_identified: bool = False
    identification: str = ""
    competing: list[dict[str, Any]] = Field(default_factory=list)
    evidence_summary: dict[str, Any] = Field(default_factory=dict)
    refutation: list[dict[str, Any]] = Field(default_factory=list)
    conformal: dict[str, Any] = Field(default_factory=dict)
    drift: dict[str, Any] = Field(default_factory=dict)
    action: Optional[dict[str, Any]] = None
    twin: Optional[dict[str, Any]] = None
    blast_radius: Optional[dict[str, Any]] = None
    gate: Optional[dict[str, Any]] = None
    autonomy_tier: int = 0
    decision: str = "REPORT_ONLY"
    graph_version: str = ""
    scm_version: str = ""
    model_version: str = ""
    created_at: float = 0.0
    signer: str = ""
    signature: str = ""                 # hex Ed25519 over canonical payload
    log_index: Optional[int] = None     # position in the transparency log
    log_root: str = ""                  # Merkle root after inclusion


class Outcome(BaseModel):
    cert_id: str
    true_root_cause: Optional[str] = None
    action_executed: bool = False
    action_outcome: Literal["resolved", "no_effect", "worsened", "n/a"] = "n/a"
    sla_minutes_lost: float = 0.0
    human_agreed: Optional[bool] = None
    verified_by: str = ""
    verified_at: float = 0.0
