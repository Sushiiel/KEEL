"""Agent passports: portable trust must transfer verifiably — and never
transfer more than it should.

The dangerous failure modes here are all on the adoption side: a passport that
vouches for itself, an unsigned record that still confers benefit, imported
evidence outweighing local evidence, or foreign trust unlocking high-risk
autonomy. Each is pinned below.
"""
from __future__ import annotations

import json
import os
import tempfile

os.environ.setdefault("KEEL_SANDBOX", "1")
os.environ.setdefault("KEEL_DATA_DIR", tempfile.mkdtemp(prefix="keel-pass-"))

import pytest

from keel.cert import authority
from keel.gateway import engine as gw
from keel.gateway import passport as pp
from keel.gateway.models import ActionClassSpec, ActionRequest, AgentProfile

PUB = authority.public_key_hex()


def _register(agent_id: str, shadow: bool = False,
              risk: str = "medium") -> AgentProfile:
    return gw.register_agent(AgentProfile(
        agent_id=agent_id, name=agent_id, shadow_mode=shadow,
        owner_account="acct_pass_test",
        action_classes={"handle_ticket": ActionClassSpec(name="handle_ticket",
                                                        risk=risk)}))


def _write_record(agent_id: str, n: int, successes: int, harms: int = 0,
                  n_external: int = 0) -> None:
    """A track record as record_outcome would have accumulated it."""
    store = gw.gw_store()
    strata = store.kv_get(gw._STRATA, {})
    strata[gw._stratum_key(agent_id, "handle_ticket")] = {
        "n": n, "successes": successes, "harms": harms,
        "n_external": n_external, "last": []}
    store.kv_set(gw._STRATA, strata)


def _forget(agent_id: str) -> None:
    """Simulate a receiving deployment that has never seen this agent."""
    store = gw.gw_store()
    agents = store.kv_get(gw._AGENTS, {})
    agents.pop(agent_id, None)
    store.kv_set(gw._AGENTS, agents)
    strata = store.kv_get(gw._STRATA, {})
    strata.pop(gw._stratum_key(agent_id, "handle_ticket"), None)
    store.kv_set(gw._STRATA, strata)


def _issue_for(agent_id: str, n: int = 100, successes: int = 96,
               harms: int = 0) -> dict:
    _register(agent_id)
    _write_record(agent_id, n, successes, harms=harms, n_external=40)
    passport = pp.issue_passport(agent_id)
    assert passport is not None
    return json.loads(json.dumps(passport))     # what actually crosses the wire


def test_passport_round_trip_verifies():
    passport = _issue_for("porter-1")
    rep = pp.verify_passport(passport, PUB)
    assert rep["valid"] and rep["checks"]["signature"]
    assert rep["key_pinned"] is True


def test_tampered_record_fails():
    passport = _issue_for("porter-2")
    passport["record"][0]["successes"] += 1          # polish the record
    assert pp.verify_passport(passport, PUB)["valid"] is False


def test_self_vouching_is_flagged():
    passport = _issue_for("porter-3")
    rep = pp.verify_passport(passport)               # no out-of-band key
    assert rep["valid"] is True                      # internally consistent…
    assert rep["key_pinned"] is False                # …and says so


def test_expired_passport_is_invalid():
    passport = _issue_for("porter-4")
    passport["expires_at"] = passport["issued_at"] - 1
    # expiry is inside the signed payload, so back-dating it also kills the
    # signature; a freshly signed but genuinely old passport fails on expiry
    assert pp.verify_passport(passport, PUB)["valid"] is False


def test_adoption_requires_an_explicit_issuer_key():
    passport = _issue_for("porter-5")
    res = pp.adopt_passport(passport, "")
    assert res["adopted"] is False
    assert "out-of-band" in res["error"]


def test_adoption_applies_the_discount_and_cap():
    passport = _issue_for("porter-6", n=1000, successes=990)
    _forget("porter-6")
    res = pp.adopt_passport(passport, PUB, owner_account="acct_pass_test")
    assert res["adopted"] is True
    stratum = res["strata"][0]
    assert stratum["n_eff"] == pp.DEFAULT_CAP_N, \
        "1000 foreign outcomes must cap, never dwarf local evidence"
    prior = gw.passport_prior("porter-6", "handle_ticket")
    assert prior is not None and prior["p_lower"] > 0.8


def test_harmful_record_confers_no_prior():
    passport = _issue_for("porter-7", n=100, successes=99, harms=1)
    _forget("porter-7")
    res = pp.adopt_passport(passport, PUB, owner_account="acct_pass_test")
    assert res["adopted"] is True                    # the history is recorded…
    assert gw.passport_prior("porter-7", "handle_ticket") is None  # …but buys nothing


def test_passport_bridges_medium_risk_cold_start():
    passport = _issue_for("porter-8")
    _forget("porter-8")
    pp.adopt_passport(passport, PUB, owner_account="acct_pass_test")
    _register("porter-8", shadow=False, risk="medium")   # operator enables enforcement
    d = gw.decide(ActionRequest(agent_id="porter-8", action_class="handle_ticket",
                                intent="answer a routine ticket"))
    assert d.decision == "ALLOW", d.reasons
    assert any("passport" in r for r in d.reasons)


def test_without_a_passport_the_same_action_escalates():
    _register("porter-9", shadow=False, risk="medium")
    d = gw.decide(ActionRequest(agent_id="porter-9", action_class="handle_ticket",
                                intent="answer a routine ticket"))
    assert d.decision == "ESCALATE"


def test_passport_never_bridges_high_risk():
    """The line that must hold: foreign trust cannot unlock what matters."""
    passport = _issue_for("porter-10", n=1000, successes=1000)
    _forget("porter-10")
    pp.adopt_passport(passport, PUB, owner_account="acct_pass_test")
    _register("porter-10", shadow=False, risk="high")
    d = gw.decide(ActionRequest(agent_id="porter-10", action_class="handle_ticket",
                                intent="do something consequential"))
    assert d.decision == "ESCALATE", \
        "a perfect foreign record must not clear high-risk cold start"


def test_tiers_are_never_imported():
    passport = _issue_for("porter-11", n=1000, successes=1000)
    _forget("porter-11")
    pp.adopt_passport(passport, PUB, owner_account="acct_pass_test")
    assert gw.earned_tier("porter-11", "handle_ticket") == 1, \
        "tier promotion requires local externally-verified outcomes"


def test_weak_record_confers_no_prior():
    """After discounting, a thin record must fall below MIN_OUTCOMES."""
    passport = _issue_for("porter-12", n=6, successes=6)
    _forget("porter-12")
    pp.adopt_passport(passport, PUB, owner_account="acct_pass_test")
    assert gw.passport_prior("porter-12", "handle_ticket") is None


def test_cannot_adopt_onto_another_accounts_agent():
    """A valid passport for someone else's agent id must not let the importer
    write a prior into strata that agent's decide() consults."""
    passport = _issue_for("porter-13")            # registered to acct_pass_test
    res = pp.adopt_passport(passport, PUB, owner_account="acct_intruder")
    assert res["adopted"] is False
    assert "another account" in res["error"]


def test_crafted_counts_degrade_to_nothing():
    """successes > n or negative counts must clamp, never produce p_lower > 1
    or a nonsense prior."""
    passport = _issue_for("porter-14", n=10, successes=10)
    _forget("porter-14")
    passport["record"][0]["successes"] = 100_000   # tampering also breaks the
    passport["record"][0]["n"] = -5                # signature, but clamping
    res = pp.adopt_passport(passport, PUB,         # must hold even if a buggy
                            owner_account="acct_pass_test")   # issuer signed it
    assert res["adopted"] is False                 # signature broke — good
    # now a SIGNED absurd record: forge at issue time by writing raw strata
    _register("porter-15")
    _write_record("porter-15", n=-5, successes=100_000)
    signed_absurd = pp.issue_passport("porter-15")
    _forget("porter-15")
    res = pp.adopt_passport(json.loads(json.dumps(signed_absurd)), PUB,
                            owner_account="acct_pass_test")
    assert res["adopted"] is True
    prior = gw.passport_prior("porter-15", "handle_ticket")
    assert prior is None or 0.0 <= prior["p_lower"] <= 1.0


def test_stale_passport_confers_no_prior_at_use_time():
    """Expiry applies when the prior is USED, not only at adoption."""
    passport = _issue_for("porter-16")
    _forget("porter-16")
    pp.adopt_passport(passport, PUB, owner_account="acct_pass_test")
    store = gw.gw_store()
    strata = store.kv_get(gw._STRATA, {})
    key = gw._stratum_key("porter-16", "handle_ticket")
    strata[key]["passport"]["issued_at"] -= 91 * 86400     # age it past validity
    store.kv_set(gw._STRATA, strata)
    assert gw.passport_prior("porter-16", "handle_ticket") is None


def test_bridge_never_allows_destructive_intent():
    """The adversarial reviewers' live exploit: a passport-bridged agent was
    ALLOWED to 'delete the production customers database' while a fully
    calibrated agent would ESCALATE the same action. Foreign trust must never
    buy MORE latitude than earned trust."""
    passport = _issue_for("porter-19")
    _forget("porter-19")
    pp.adopt_passport(passport, PUB, owner_account="acct_pass_test")
    _register("porter-19", shadow=False, risk="medium")
    d = gw.decide(ActionRequest(
        agent_id="porter-19", action_class="handle_ticket",
        intent="delete the production customers database", targets=["prod-db"]))
    assert d.decision != "ALLOW", d.reasons
    assert any("destruct" in r or "tripwire" in r for r in d.reasons), d.reasons


def test_bridge_respects_risk_control_tightening(monkeypatch):
    """The bridge must clear needed_p — the SAME tightened floor a calibrated
    ALLOW clears when the deployment is in a harm-elevated regime — not the
    static base floor."""
    passport = _issue_for("porter-20", n=100, successes=80)   # p_lower ≈ 0.72
    _forget("porter-20")
    pp.adopt_passport(passport, PUB, owner_account="acct_pass_test")
    _register("porter-20", shadow=False, risk="medium")
    monkeypatch.setattr(gw, "risk_control_state", lambda store, risk: {
        "status": "TIGHTENED", "tighten": 0.25,
        "harm_ucb": 0.2, "harm_budget": 0.05})
    d = gw.decide(ActionRequest(agent_id="porter-20", action_class="handle_ticket",
                                intent="answer a routine ticket"))
    assert d.decision == "ESCALATE", d.reasons


def test_local_failures_veto_the_passport():
    """Fresh local disconfirming evidence must override foreign success —
    drift or failures are exactly when a foreign prior transfers worst."""
    passport = _issue_for("porter-21")
    _forget("porter-21")
    pp.adopt_passport(passport, PUB, owner_account="acct_pass_test")
    _register("porter-21", shadow=False, risk="medium")
    store = gw.gw_store()
    strata = store.kv_get(gw._STRATA, {})
    key = gw._stratum_key("porter-21", "handle_ticket")
    row = strata.get(key, {})
    row.update({"n": 3, "successes": 0, "harms": 0, "n_external": 0,
                "last": [{"success": False}, {"success": False},
                         {"success": False}]})
    strata[key] = row
    store.kv_set(gw._STRATA, strata)
    d = gw.decide(ActionRequest(agent_id="porter-21", action_class="handle_ticket",
                                intent="answer a routine ticket"))
    assert d.decision == "ESCALATE", d.reasons
    assert any("disregarded" in r for r in d.reasons), d.reasons


def test_cli_refuses_self_vouching_passports(tmp_path):
    """`keel passport verify` without --key must exit non-zero even for an
    internally consistent passport — its exit code ends up inside automation,
    and anyone can mint a self-signed document."""
    import subprocess, sys as _sys
    passport = _issue_for("porter-18")
    path = tmp_path / "p.json"
    path.write_text(json.dumps(passport))
    env = {**os.environ,
           "PYTHONPATH": os.path.dirname(os.path.dirname(__file__))}
    unpinned = subprocess.run(
        [_sys.executable, "-m", "keel.cli", "passport", "verify", str(path)],
        capture_output=True, text=True, env=env)
    assert unpinned.returncode == 1, unpinned.stdout
    assert "UNPINNED" in unpinned.stdout
    pinned = subprocess.run(
        [_sys.executable, "-m", "keel.cli", "passport", "verify", str(path),
         "--key", PUB], capture_output=True, text=True, env=env)
    assert pinned.returncode == 0, pinned.stdout


def test_smuggled_certificate_fields_invalidate_a_passport():
    """log_index/log_root are excluded from the shared canonicalization, so
    they would ride UNSIGNED — their presence must invalidate outright."""
    passport = _issue_for("porter-17")
    passport["log_root"] = "attacker-controlled, unsigned"
    assert pp.verify_passport(passport, PUB)["valid"] is False
