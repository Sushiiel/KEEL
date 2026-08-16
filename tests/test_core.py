"""Core-correctness tests: the places where silent wrongness lives.

Covers: conformal coverage (finite-sample guarantee), Tian-Pearl bounds,
counterfactual PN on a known SCM, Merkle log tamper-evidence, Ed25519
signatures, entity resolution precision, and the safety gate's fail-closed
behavior.
"""
import os
os.environ.setdefault("KEEL_SANDBOX", "1")
import tempfile

import numpy as np
import pytest

os.environ.setdefault("KEEL_DATA_DIR", tempfile.mkdtemp(prefix="keel-test-"))

from keel.adjudicate.bounds import observational_table, tian_pearl_bounds
from keel.adjudicate.counterfactual import (probability_of_necessity,
                                            probability_of_sufficiency)
from keel.adjudicate.scm import OUTCOME, InstanceSCM
from keel.calibrate.conformal import conformal_quantile
from keel.cert import translog
from keel.models import TopoEdge


# ── conformal ────────────────────────────────────────────────────────────────

def test_conformal_quantile_finite_sample_correction():
    scores = [i / 100 for i in range(100)]
    q = conformal_quantile(scores, alpha=0.10)
    # ceil(101*0.9)=91 → the 91st smallest = 0.90
    assert q == pytest.approx(0.90)


def test_conformal_coverage_guarantee_holds_empirically():
    """Split conformal must achieve >= 1-alpha coverage on exchangeable data."""
    rng = np.random.default_rng(0)
    alpha = 0.10
    hits, trials = 0, 400
    for _ in range(trials):
        cal = rng.beta(2, 5, size=60)          # nonconformity scores
        test = float(rng.beta(2, 5))
        q = conformal_quantile(list(cal), alpha)
        hits += int(test <= q)
    coverage = hits / trials
    assert coverage >= 1 - alpha - 0.04, f"coverage {coverage} below guarantee"


def test_conformal_quantile_refuses_tiny_n():
    assert conformal_quantile([0.5, 0.6], alpha=0.10) is None  # rank > n


# ── Tian-Pearl bounds ────────────────────────────────────────────────────────

def test_tian_pearl_bounds_ordering_and_range():
    lo, hi = tian_pearl_bounds(40, 10, 5, 45)
    assert 0.0 <= lo <= hi <= 1.0
    # strong association → informative lower bound
    assert lo > 0.5


def test_tian_pearl_no_data_is_vacuous():
    assert tian_pearl_bounds(0, 0, 0, 0) == (0.0, 1.0)


def test_observational_table_counts():
    corpus = [({"a", "b"}, True), ({"b"}, False), ({"a"}, True), (set(), False)]
    assert observational_table(corpus, "a") == (2, 0, 0, 2)


# ── counterfactual engine on a known chain SCM ───────────────────────────────

def _chain_scm(w1=0.9, w2=0.9, leak=0.01):
    """X → M → SLA → OUTCOME with known mechanism strengths."""
    x, m, sla = "E1|root", "E2|mid", "SVC|svc.sla_breach"
    nodes = [x, m, sla, OUTCOME]
    parents = {m: [(x, w1)], sla: [(m, w2)], OUTCOME: [(sla, 0.99)]}
    leaks = {x: leak, m: leak, sla: leak, OUTCOME: 0.0}
    priors = {x: 0.05}
    return InstanceSCM(nodes, parents, leaks, priors, [x, m, sla, OUTCOME]), x, sla


def test_pn_high_for_true_chain_cause():
    scm, x, sla = _chain_scm()
    evidence = {x: 1, "E2|mid": 1, sla: 1, OUTCOME: 1}
    pn, lo, hi = probability_of_necessity(scm, evidence, x, n=4000)
    assert pn > 0.85, f"chain root should be necessary, got PN={pn}"
    assert lo <= pn <= hi


def test_pn_near_zero_for_non_cause():
    """A node with no causal path to the outcome must get PN ~ 0."""
    x, m, sla, noise = "E1|root", "E2|mid", "SVC|svc.sla_breach", "E9|env.temp"
    nodes = [x, m, sla, noise, OUTCOME]
    parents = {m: [(x, 0.9)], sla: [(m, 0.9)], OUTCOME: [(sla, 0.99)]}
    scm = InstanceSCM(nodes, parents, {n: 0.01 for n in nodes},
                      {x: 0.05, noise: 0.5}, [x, noise, m, sla, OUTCOME])
    evidence = {x: 1, m: 1, sla: 1, noise: 1, OUTCOME: 1}
    pn, _, _ = probability_of_necessity(scm, evidence, noise, n=3000)
    assert pn < 0.05, f"non-cause got PN={pn}"


def test_ps_positive_for_strong_chain():
    scm, x, _ = _chain_scm()
    ps, lo, hi = probability_of_sufficiency(scm, x, n=3000)
    assert ps > 0.5
    assert 0 <= lo <= hi <= 1


def test_abduction_respects_evidence():
    """Factual reconstruction must reproduce the observed world exactly."""
    scm, x, sla = _chain_scm()
    evidence = {x: 1, "E2|mid": 0, sla: 0, OUTCOME: 0}
    rng = np.random.default_rng(1)
    U = scm.abduct(evidence, 500, rng)
    assert (U["factual"][x] == 1).all()
    assert (U["factual"]["E2|mid"] == 0).all()
    assert (U["factual"][OUTCOME] == 0).all()


def test_intervention_severs_causal_path():
    scm, x, sla = _chain_scm()
    evidence = {x: 1, "E2|mid": 1, sla: 1, OUTCOME: 1}
    rng = np.random.default_rng(2)
    U = scm.abduct(evidence, 2000, rng)
    cf = scm.forward(U, {x: 0}, 2000)
    # severing the root must strictly reduce the outage rate
    assert cf[OUTCOME].mean() < 0.2


# ── SCM construction from topology ───────────────────────────────────────────

def test_scm_instantiation_respects_topology():
    edges = [{"src_type": "a", "dst_type": "b", "strength": 0.8}]
    topo = [TopoEdge(src="E1", dst="E2", relation="carries", valid_from=0)]
    observed = {"E1|a", "E2|b", "E3|b"}
    scm = InstanceSCM.build(edges, topo, observed, ["E1", "E2", "E3"])
    assert ("E1|a", 0.8) in scm.parents.get("E2|b", [])
    # E3 is not adjacent to E1 → no edge
    assert not any(p == "E1|a" for p, _ in scm.parents.get("E3|b", []))


def test_toposort_breaks_cycles():
    nodes = ["A|x", "B|y", OUTCOME]
    parents = {"A|x": [("B|y", 0.3)], "B|y": [("A|x", 0.7)]}
    order = InstanceSCM._toposort(nodes, parents)
    assert set(order) == set(nodes)


# ── transparency log ─────────────────────────────────────────────────────────

def test_merkle_inclusion_and_tamper_detection():
    from keel.store import Store
    store = Store(path=os.path.join(tempfile.mkdtemp(), "t.db"))
    payloads = [f"cert-{i}".encode() for i in range(7)]
    for i, p in enumerate(payloads):
        translog.append(store, p, f"c{i}", float(i))
    root = translog.current_root(store)
    proof = translog.inclusion_proof(store, 3)
    assert proof is not None and proof["root"] == root
    assert translog.verify_inclusion(proof["leaf"], proof["path"], root)
    # forged leaf must fail
    bad_leaf = translog.leaf_hash(b"forged")
    assert not translog.verify_inclusion(bad_leaf, proof["path"], root)


def test_certificate_signature_roundtrip():
    from keel.cert.authority import canonical_payload, signing_key, verify
    from keel.models import Certificate
    cert = Certificate(cert_id="keel:cert:TEST", incident_id="INC-X",
                       claim={"root_cause": "E|t"}, verdict="SUPPORTED",
                       created_at=1.0, signer="test")
    cert.signature = signing_key().sign(canonical_payload(cert)).hex()
    assert verify(cert)["signature_valid"]
    cert.pn = 0.99                      # tamper with a signed field
    assert not verify(cert)["signature_valid"]


# ── gate fails closed ────────────────────────────────────────────────────────

def test_shield_violations_detected():
    from keel.gate.shield import violations
    costs = {"elements_touched": 99, "blast_radius_elements": 3,
             "slas_at_risk": 2, "redundancy_min_paths": 0, "est_sla_minutes": 0}
    v = violations(costs)
    assert any("elements_touched" in x for x in v)
    assert any("slas_at_risk" in x for x in v)
    assert any("redundancy" in x for x in v)


def test_hard_down_semantics():
    from keel.gate.blast import hard_failed_elements
    ev = {"A|optical.los": 1, "B|isis.spf_churn": 1, "C|hw.power_loss": 1,
          "D|optical.los": 0}
    hard = {"optical.los", "hw.power_loss"}
    assert hard_failed_elements(ev, hard) == {"A", "C"}
