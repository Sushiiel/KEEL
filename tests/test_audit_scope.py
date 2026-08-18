"""The audit pack must never disclose another account's data.

This mattered less when most users only received a 3-decision "preview". Now
that every feature is free, every signed-in user gets the FULL export — so an
unscoped pack hands over every tenant's certificates, calibration tables and,
worst of all, the identities of the humans who approved their escalations.

Fixtures go through the real registration and decision paths rather than
hand-writing store rows, so this test also catches a regression in the
certificate -> decision -> agent -> owner_account attribution chain that the
scoping depends on.
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("KEEL_SANDBOX", "1")
os.environ["KEEL_DATA_DIR"] = tempfile.mkdtemp(prefix="keel-auditscope-")

import pytest

from keel.gateway import engine as gw
from keel.gateway.audit import build_audit_pack
from keel.gateway.models import ActionClassSpec, ActionRequest, AgentProfile

ACCT_A = "acct_aaaaaaaaaaaaaaaa"
ACCT_B = "acct_bbbbbbbbbbbbbbbb"


def _register(agent_id: str, account: str) -> AgentProfile:
    return gw.register_agent(AgentProfile(
        agent_id=agent_id, name=f"{agent_id}-name", owner_account=account,
        framework="custom",
        action_classes={"send_email": ActionClassSpec(name="send_email",
                                                     risk="low")}))


def _decide(agent_id: str, intent: str):
    return gw.decide(ActionRequest(agent_id=agent_id, action_class="send_email",
                                   intent=intent, targets=["someone@example.com"],
                                   reversible=True))


@pytest.fixture(scope="module", autouse=True)
def _two_tenants():
    _register("agent-of-a", ACCT_A)
    _register("agent-of-b", ACCT_B)
    for i in range(4):
        _decide("agent-of-a", f"a-side action {i}")
        _decide("agent-of-b", f"b-side action {i}")
    # a human releases one of B's escalations, so an approver identity exists
    decs = [d for d in gw.recent_decisions(500) if d.agent_id == "agent-of-b"]
    assert decs, "fixture produced no decisions for account B"
    store = gw.gw_store()
    raw = store.kv_get(gw._DECISIONS, {})
    target = decs[0]
    raw[target.request_id]["approved_by"] = "approver-b@example.com"
    store.kv_set(gw._DECISIONS, raw)
    yield


def _cert_ids(pack) -> set[str]:
    return {s["certificate"]["cert_id"] for s in pack["sampled_decisions"]}


def _agents_in(pack) -> set[str]:
    return {row["agent"] for row in pack["calibration_tables"]}


def test_account_pack_contains_only_its_own_agents():
    pack = build_audit_pack(sample_size=50, account_id=ACCT_A)
    assert _agents_in(pack) == {"agent-of-a"}
    assert "agent-of-b" not in _agents_in(pack)


def test_account_pack_leaks_no_foreign_certificates():
    a = build_audit_pack(sample_size=50, account_id=ACCT_A)
    b = build_audit_pack(sample_size=50, account_id=ACCT_B)
    assert _cert_ids(a), "account A should have certificates of its own"
    assert _cert_ids(b), "account B should have certificates of its own"
    assert not (_cert_ids(a) & _cert_ids(b)), "certificate sets overlap"


def test_approver_identities_do_not_cross_accounts():
    """The worst field in the pack: who approved what."""
    a = build_audit_pack(sample_size=50, account_id=ACCT_A)
    approvers = {r["approved_by"] for r in a["human_oversight_record"]}
    assert "approver-b@example.com" not in approvers
    blob = repr(a)
    assert "approver-b@example.com" not in blob, "approver leaked elsewhere in the pack"
    # and B genuinely has the record, so the test above isn't vacuous
    b = build_audit_pack(sample_size=50, account_id=ACCT_B)
    assert "approver-b@example.com" in {r["approved_by"]
                                        for r in b["human_oversight_record"]}


def test_no_foreign_agent_id_anywhere_in_the_pack():
    """Belt and braces: scan the whole serialised pack, not just the sections we
    remembered to filter."""
    a = build_audit_pack(sample_size=50, account_id=ACCT_A)
    assert "agent-of-b" not in repr(a)


def test_unscoped_pack_still_sees_everything():
    """Self-host / CLI use must be unchanged — there is nothing to separate."""
    pack = build_audit_pack(sample_size=50, account_id=None)
    assert {"agent-of-a", "agent-of-b"} <= _agents_in(pack)
    assert pack["scope"]["kind"] == "deployment"


def test_scope_is_recorded_so_an_auditor_can_tell():
    pack = build_audit_pack(sample_size=50, account_id=ACCT_A)
    assert pack["scope"]["kind"] == "account"
    assert pack["scope"]["account_id"] == ACCT_A


def test_sampling_draws_from_the_scoped_population():
    """Sampling globally then filtering would silently under-fill the pack and
    make the 'uniform random' claim false for this tenant."""
    pack = build_audit_pack(sample_size=2, account_id=ACCT_A, seed=7)
    assert len(pack["sampled_decisions"]) == 2, "scoped sample was under-filled"
    assert pack["sampling"]["eligible_population"] >= 2
    # every sampled cert must belong to A
    b_certs = _cert_ids(build_audit_pack(sample_size=50, account_id=ACCT_B))
    assert not (_cert_ids(pack) & b_certs)


def test_unknown_account_gets_an_empty_but_valid_pack():
    """Fail closed: an account with no agents must get nothing, not everything."""
    pack = build_audit_pack(sample_size=50, account_id="acct_does_not_exist")
    assert pack["sampled_decisions"] == []
    assert pack["calibration_tables"] == []
    assert pack["human_oversight_record"] == []
    # the deployment-wide verifiability facts are still present
    assert pack["authority_public_key"]
    assert "root" in pack["transparency_log"]
