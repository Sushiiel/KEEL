"""Signed log checkpoints: the log going on the record.

The property that matters: two authority-signed checkpoints of the same size
with different roots are proof of a rewrite that the deployment cannot disown.
The comparison must also refuse to over-claim — growth is consistent with
honest append, not proof of it.
"""
from __future__ import annotations

import json
import os
import tempfile

os.environ.setdefault("KEEL_SANDBOX", "1")
os.environ.setdefault("KEEL_DATA_DIR", tempfile.mkdtemp(prefix="keel-ckpt-"))

import pytest

from keel.cert import authority, translog
from keel.cert.verifier import compare_checkpoints, verify_checkpoint
from keel.models import Certificate
from keel.store import get_store

PUB = authority.public_key_hex()


def _issue(store, text: str) -> None:
    authority.issue(store, Certificate(
        cert_id="", incident_id="inc-ckpt", claim={"statement": text},
        verdict="SUPPORTED"))


def test_checkpoint_signature_verifies_and_tampering_fails():
    store = get_store("ckpt-a")
    _issue(store, "one")
    cp = json.loads(json.dumps(translog.signed_checkpoint(store)))
    assert verify_checkpoint(cp, PUB) is True
    forged = dict(cp); forged["root"] = "ab" * 32
    assert verify_checkpoint(forged, PUB) is False
    wrong_schema = dict(cp); wrong_schema["schema"] = "something-else"
    assert verify_checkpoint(wrong_schema, PUB) is False


def test_identical_and_append_consistent_verdicts():
    store = get_store("ckpt-b")
    _issue(store, "one")
    a = translog.signed_checkpoint(store)
    same = translog.signed_checkpoint(store)
    assert compare_checkpoints(a, same, PUB)["verdict"] == "IDENTICAL"
    _issue(store, "two")
    b = translog.signed_checkpoint(store)
    rep = compare_checkpoints(a, b, PUB)
    assert rep["verdict"] == "APPEND-CONSISTENT"
    # must not over-claim: growth is consistent with honesty, not proof of it
    assert "not cryptographic proof" in rep["detail"]


def test_fork_is_proven_by_two_signed_checkpoints():
    """Two logs, same size, different content — as after a rewrite — and both
    checkpoints carry the same authority's real signature."""
    fork_a, fork_b = get_store("ckpt-fork-a"), get_store("ckpt-fork-b")
    _issue(fork_a, "the original history")
    _issue(fork_b, "the rewritten history")
    cp_a = translog.signed_checkpoint(fork_a)
    cp_b = translog.signed_checkpoint(fork_b)
    assert cp_a["size"] == cp_b["size"] and cp_a["root"] != cp_b["root"]
    rep = compare_checkpoints(cp_a, cp_b, PUB)
    assert rep["verdict"] == "FORK-PROOF"


def test_truncation_is_proven():
    store = get_store("ckpt-c")
    _issue(store, "one"); _issue(store, "two")
    big = translog.signed_checkpoint(store)
    small_store = get_store("ckpt-c-small")
    _issue(small_store, "one")
    small = translog.signed_checkpoint(small_store)
    assert compare_checkpoints(big, small, PUB)["verdict"] == "TRUNCATION-PROOF"


def test_swapped_arguments_do_not_fabricate_truncation():
    """Ordering comes from the SIGNED timestamps, not from which file the
    caller passed first — an honest append must not become a 'truncation
    proof' because someone reversed two filenames."""
    store = get_store("ckpt-order")
    _issue(store, "one")
    early = translog.signed_checkpoint(store)
    _issue(store, "two")
    late = translog.signed_checkpoint(store)
    rep = compare_checkpoints(late, early, PUB)      # deliberately reversed
    assert rep["verdict"] == "APPEND-CONSISTENT", rep
    assert rep.get("reordered_by_ts") is True


def test_unverifiable_input_concludes_nothing():
    store = get_store("ckpt-d")
    _issue(store, "one")
    cp = translog.signed_checkpoint(store)
    forged = dict(cp); forged["size"] = cp["size"] + 5
    rep = compare_checkpoints(cp, forged, PUB)
    assert rep["verdict"] == "UNVERIFIABLE"


def test_checkpoint_endpoint_is_public_and_signed():
    """Outsiders must be able to fetch checkpoints with no account — that is
    the whole point of publishing them."""
    from fastapi.testclient import TestClient

    from keel.server.app import app
    os.environ["KEEL_AUTH_REQUIRED"] = "1"
    try:
        r = TestClient(app).get("/api/gateway/checkpoint")
        assert r.status_code == 200
        cp = r.json()
        assert verify_checkpoint(cp, cp["public_key"]) is True
        assert set(cp) >= {"size", "root", "ts", "signature", "public_key"}
    finally:
        os.environ.pop("KEEL_AUTH_REQUIRED", None)
