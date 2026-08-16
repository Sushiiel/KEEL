"""Gateway trust-layer core: tripwires always, citation integrity, earned
autonomy from external outcomes only, fail-closed defaults."""
import os, tempfile
os.environ.setdefault("KEEL_SANDBOX", "1")
os.environ.setdefault("KEEL_DATA_DIR", tempfile.mkdtemp(prefix="keel-gw-"))

from keel.gateway import engine as gw
from keel.gateway.models import (ActionClassSpec, ActionOutcome, ActionRequest,
                                 AgentProfile)


def _agent(shadow=False, risk="high", **spec):
    gw.register_agent(AgentProfile(
        agent_id="t-bot", name="T", shadow_mode=shadow,
        action_classes={"act": ActionClassSpec(name="act", risk=risk, **spec)}))


def test_unknown_agent_blocked():
    d = gw.decide(ActionRequest(agent_id="ghost", action_class="act"))
    assert d.decision == "BLOCK"


def test_undeclared_action_class_blocked():
    _agent()
    d = gw.decide(ActionRequest(agent_id="t-bot", action_class="never_declared"))
    assert d.decision == "BLOCK"


def test_tripwire_blocks_even_in_shadow():
    _agent(shadow=True)
    d = gw.decide(ActionRequest(agent_id="t-bot", action_class="act",
                                payload={"cmd": "rm -rf /var/data"}))
    assert d.decision == "BLOCK"
    assert any(c.checker == "tripwire" and c.verdict == "fail" for c in d.checks)


def test_citation_integrity_catches_fabrication():
    _agent(shadow=False, requires_evidence=True)
    d = gw.decide(ActionRequest(
        agent_id="t-bot", action_class="act",
        claims=[{"statement": "revenue grew 47% to $12.4M",
                 "evidence_refs": ["r1"]}],
        evidence=[{"ref": "r1", "content": "revenue was $9.1M, up 18%"}]))
    assert d.decision == "BLOCK"


def test_cold_start_high_risk_escalates_and_external_outcomes_earn_tier():
    _agent(shadow=False, risk="medium")
    d = gw.decide(ActionRequest(agent_id="t-bot", action_class="act"))
    assert d.decision == "ESCALATE"          # no calibration yet at medium risk
    # self-reported outcomes never promote; external ones do
    for i in range(12):
        di = gw.decide(ActionRequest(agent_id="t-bot", action_class="act"))
        gw.record_outcome(ActionOutcome(request_id=di.request_id, success=True,
                                        reported_by="agent"))
    assert gw.earned_tier("t-bot", "act") == 1
    for i in range(6):
        di = gw.decide(ActionRequest(agent_id="t-bot", action_class="act"))
        gw.record_outcome(ActionOutcome(request_id=di.request_id, success=True,
                                        reported_by="human-review"))
    assert gw.earned_tier("t-bot", "act") >= 2
    d = gw.decide(ActionRequest(agent_id="t-bot", action_class="act"))
    assert d.decision == "ALLOW"


def test_budget_cap_blocks():
    _agent(shadow=False, risk="low", budget_per_day=100.0)
    a = gw.decide(ActionRequest(agent_id="t-bot", action_class="act", cost=80))
    assert a.decision == "ALLOW"
    b = gw.decide(ActionRequest(agent_id="t-bot", action_class="act", cost=50))
    assert b.decision == "BLOCK"


def test_every_decision_is_signed_and_logged():
    _agent(shadow=True, risk="low")
    d = gw.decide(ActionRequest(agent_id="t-bot", action_class="act"))
    cert = gw.gw_store().certificate(d.cert_id)
    assert cert is not None and cert.signature and cert.log_index is not None


def test_drift_resets_autonomy():
    """A regime change in the outcome stream must void earned autonomy."""
    from keel.gateway.adaptive import adaptive_window
    good = [{"ts": i, "success": True, "harm": False} for i in range(30)]
    bad = [{"ts": 30 + i, "success": False, "harm": False} for i in range(12)]
    window, drifted = adaptive_window(good + bad)
    assert drifted, "Page-Hinkley must detect the success->failure regime change"
    assert sum(1 for o in window if o["success"]) < 5


def test_anomaly_scoring_flags_unusual_transition():
    from keel.gateway.adaptive import anomaly_score, observe_transition
    from keel.gateway.engine import gw_store
    st = gw_store()
    for _ in range(30):
        observe_transition(st, "anom-bot", "read_docs")
        observe_transition(st, "anom-bot", "summarize")
    # last observed action is 'summarize'; the habitual NEXT step is read_docs
    excess, _ = anomaly_score(st, "anom-bot", "read_docs")
    assert excess < 1.0                       # habitual transition: unsurprising
    excess2, _ = anomaly_score(st, "anom-bot", "export_credentials")
    assert excess2 >= 3.0                     # never-seen jump: highly surprising


def test_audit_pack_is_self_verifying():
    from keel.gateway.audit import build_audit_pack
    from keel.cert import translog
    _agent(shadow=True, risk="low")
    for _ in range(3):
        gw.decide(ActionRequest(agent_id="t-bot", action_class="act"))
    pack = build_audit_pack(sample_size=5, seed=1)
    assert pack["transparency_log"]["chain_consistent"]
    assert len(pack["sampled_decisions"]) >= 1
    for s in pack["sampled_decisions"]:
        assert s["signature_verification"]["signature_valid"]
        proof = s["inclusion_proof"]
        assert translog.verify_inclusion(proof["leaf"], proof["path"], proof["root"])
    assert pack["honest_limits"]
