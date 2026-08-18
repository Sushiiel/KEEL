"""Counterfactual policy replay: the impact report must be truthful.

Decisions here are produced through the real decide() pipeline so replay is
tested against genuine recorded evidence, not hand-built rows.
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("KEEL_SANDBOX", "1")
os.environ.setdefault("KEEL_DATA_DIR", tempfile.mkdtemp(prefix="keel-replay-"))

import pytest

from keel.gateway import engine as gw
from keel.gateway import replay as rp
from keel.gateway.models import ActionClassSpec, ActionRequest, AgentProfile


@pytest.fixture(scope="module", autouse=True)
def _history():
    gw.register_agent(AgentProfile(
        agent_id="replay-bot", name="Replay Bot", shadow_mode=False,
        owner_account="acct_replay_a",
        action_classes={
            "send_reply": ActionClassSpec(name="send_reply", risk="low"),
            "update_crm": ActionClassSpec(name="update_crm", risk="medium")}))
    gw.register_agent(AgentProfile(
        agent_id="other-tenant-bot", name="Other", shadow_mode=True,
        owner_account="acct_replay_b",
        action_classes={"send_reply": ActionClassSpec(name="send_reply",
                                                     risk="low")}))
    for i in range(6):
        gw.decide(ActionRequest(agent_id="replay-bot", action_class="send_reply",
                                intent=f"reply {i}"))          # low risk → ALLOW
    for i in range(3):
        gw.decide(ActionRequest(agent_id="replay-bot", action_class="update_crm",
                                intent=f"update {i}"))         # cold medium → ESCALATE
    for i in range(4):
        gw.decide(ActionRequest(agent_id="other-tenant-bot",
                                action_class="send_reply", intent=f"x {i}"))
    yield


def test_no_change_means_no_flips():
    rep = rp.replay({}, account_id="acct_replay_a")
    assert rep["summary"]["would_tighten"] == 0
    assert rep["summary"]["would_loosen"] == 0
    assert rep["replayed"] >= 9


def test_raising_a_class_to_high_flips_allows_to_escalate():
    rep = rp.replay({"risk_overrides": {"send_reply": "high"}},
                    account_id="acct_replay_a")
    flips = [f for f in rep["flips"] if f["action_class"] == "send_reply"]
    assert flips, "reclassifying the class must flip its cold-start ALLOWs"
    assert all(f["was"] == "ALLOW" and f["would_be"] == "ESCALATE" for f in flips)
    assert rep["summary"]["would_tighten"] == len(flips)


def test_unknown_knob_is_an_error_not_a_noop():
    """A typo'd knob that silently 'worked' would green-light an untested
    policy — the most dangerous possible outcome of a safety tool."""
    rep = rp.replay({"risk_overides": {"send_reply": "high"}})   # typo
    assert "error" in rep and "risk_overides" in rep["error"]


def test_invalid_risk_level_is_rejected():
    assert "error" in rp.replay({"risk_overrides": {"send_reply": "extreme"}})


def test_replay_is_scoped_to_the_account():
    rep = rp.replay({}, account_id="acct_replay_a")
    agents = {f["agent"] for f in rep["flips"]} | {
        d.agent_id for d in gw.recent_decisions(500)
        if gw.agent_owner(d.agent_id) == "acct_replay_a"}
    assert "other-tenant-bot" not in agents
    total_a = sum(1 for d in gw.recent_decisions(500)
                  if gw.agent_owner(d.agent_id) == "acct_replay_a")
    assert rep["replayed"] == total_a


def test_enforce_shadow_previews_shadow_exit():
    rep = rp.replay({"enforce_shadow": True}, account_id="acct_replay_b")
    assert rep["replayed"] >= 4
    # shadow decisions recompute to what enforcement would do (low-risk cold
    # start → ALLOW), so the transition matrix shows SHADOW→ALLOW
    assert any(k.startswith("SHADOW→") for k in rep["transitions"])


def test_replay_has_no_side_effects():
    before = len(gw.recent_decisions(500))
    root_before = __import__("keel.cert.translog", fromlist=["current_root"]) \
        .current_root(gw.gw_store())
    rp.replay({"risk_overrides": {"send_reply": "critical"}, "floor_delta": 0.2})
    assert len(gw.recent_decisions(500)) == before, "replay stored a decision"
    root_after = __import__("keel.cert.translog", fromlist=["current_root"]) \
        .current_root(gw.gw_store())
    assert root_after == root_before, "replay wrote to the transparency log"


def test_report_states_its_limits():
    rep = rp.replay({})
    assert any("upper bound" in l for l in rep["honest_limits"])
    assert any("behaviour" in l for l in rep["honest_limits"])


def test_shadow_with_hard_fail_is_not_a_fabricated_block():
    """decide() records SHADOW even when checks hard-fail — that is what
    shadow mode is. An identity replay must reproduce SHADOW, not invent a
    SHADOW→BLOCK flip from the recorded failure."""
    gw.register_agent(AgentProfile(
        agent_id="shadow-fail-bot", name="SF", shadow_mode=True,
        owner_account="acct_replay_c",
        action_classes={"claim": ActionClassSpec(name="claim", risk="medium",
                                                 requires_evidence=True)}))
    from keel.gateway.models import Claim
    d = gw.decide(ActionRequest(
        agent_id="shadow-fail-bot", action_class="claim",
        intent="assert something without evidence",
        claims=[Claim(statement="the total is 42", evidence_refs=["e9"])],
        evidence=[]))                                  # grounding hard-fails
    assert d.decision == "SHADOW"
    assert any(c.verdict == "fail" for c in d.checks), \
        "fixture must actually contain a recorded hard-fail"
    rep = rp.replay({}, account_id="acct_replay_c")
    assert rep["summary"]["would_tighten"] == 0
    assert not any(f["was"] == "SHADOW" and f["would_be"] == "BLOCK"
                   for f in rep["flips"])
    # under enforce_shadow the same evidence MUST surface as a block
    rep2 = rp.replay({"enforce_shadow": True}, account_id="acct_replay_c")
    assert any(k == "SHADOW→BLOCK" for k in rep2["transitions"])
