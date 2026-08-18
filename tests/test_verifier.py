"""The offline verifier is KEEL's core promise made testable: a certificate
must verify with nothing but its JSON and a public key — and any alteration of
content, key, or proof must fail.

Certificates here are issued through the REAL authority and transparency log,
then serialized to plain JSON exactly as the API emits them. That pins the
canonicalization contract between authority.canonical_payload and
verifier.canonical_payload; if the two ever drift, every third-party
verification breaks, and this file is what catches it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

os.environ.setdefault("KEEL_SANDBOX", "1")
os.environ.setdefault("KEEL_DATA_DIR", tempfile.mkdtemp(prefix="keel-verif-"))

import pytest

from keel.cert import authority, translog, verifier
from keel.models import Certificate
from keel.store import get_store

STORE = get_store("verifier-test")
PUB = authority.public_key_hex()


def _issue(statement: str) -> Certificate:
    return authority.issue(STORE, Certificate(
        cert_id="", incident_id="inc-verif",
        claim={"statement": statement}, verdict="SUPPORTED"))


@pytest.fixture(scope="module")
def bundle() -> dict:
    """A certificate exactly as the API would hand it to a user."""
    cert = _issue("the primary cause is a fabricated demo event")
    _issue("a second entry so the Merkle tree has real siblings")
    _issue("and a third")
    proof = translog.inclusion_proof(STORE, cert.log_index)
    # round-trip through JSON: what a user's file actually contains
    return json.loads(json.dumps(
        {"certificate": cert.model_dump(), "inclusion_proof": proof}))


def test_live_issued_certificate_verifies_offline(bundle):
    report = verifier.verify(bundle, PUB)
    assert report["checks"]["signature"] is True
    assert report["checks"]["inclusion_proof"] is True
    assert report["checks"]["leaf_matches_payload"] is True
    assert report["valid"] is True


def test_bare_certificate_without_proof_is_signature_only(bundle):
    report = verifier.verify(bundle["certificate"], PUB)
    assert report["checks"]["signature"] is True
    assert report["checks"]["inclusion_proof"] is None, \
        "an absent proof must be reported absent, never passed"
    assert report["valid"] is True


def test_any_field_change_breaks_the_signature(bundle):
    for field, value in [("verdict", "REFUTED"),
                         ("claim", {"statement": "something else"}),
                         ("signer", "impostor")]:
        tampered = json.loads(json.dumps(bundle))
        tampered["certificate"][field] = value
        report = verifier.verify(tampered, PUB)
        assert report["checks"]["signature"] is False, field
        assert report["valid"] is False, field


def test_wrong_public_key_fails():
    cert = _issue("verify against the wrong key")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    other = Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    assert verifier.verify(cert.model_dump(), other)["valid"] is False


def test_proof_for_a_different_certificate_is_rejected(bundle):
    """A valid proof must not transfer: swapping in another entry's proof has
    to fail on the leaf check even though the path itself is consistent."""
    other = _issue("a different certificate with its own proof")
    stolen = translog.inclusion_proof(STORE, other.log_index)
    swapped = {"certificate": bundle["certificate"], "inclusion_proof": stolen}
    report = verifier.verify(json.loads(json.dumps(swapped)), PUB)
    assert report["checks"]["leaf_matches_payload"] is False
    assert report["valid"] is False


def test_root_pinning(bundle):
    good_root = bundle["inclusion_proof"]["root"]
    assert verifier.verify(bundle, PUB, expected_root=good_root)["valid"] is True
    bad = verifier.verify(bundle, PUB, expected_root="ab" * 32)
    assert bad["checks"]["root_pinned"] is False
    assert bad["valid"] is False


def test_garbage_input_reports_instead_of_raising():
    for junk in ({}, {"certificate": {}}, {"certificate": {"signature": "zz"}},
                 {"signature": ""}):
        report = verifier.verify(junk, PUB)
        assert report["valid"] is False


def test_verifier_module_is_dependency_free():
    """The whole point: it must be runnable where KEEL is not deployed.
    Anything beyond stdlib + the Ed25519 primitive breaks that."""
    import ast, inspect
    tree = ast.parse(inspect.getsource(verifier))
    imported = {n.module.split(".")[0] if isinstance(n, ast.ImportFrom) and n.module
                else a.name.split(".")[0]
                for n in ast.walk(tree)
                if isinstance(n, (ast.Import, ast.ImportFrom))
                for a in (n.names if isinstance(n, ast.Import) else [None])}
    imported.discard(None)
    assert imported <= {"hashlib", "json", "typing", "cryptography", "__future__"}, \
        f"verifier grew a heavy dependency: {imported}"


def test_cli_verify_end_to_end(bundle, tmp_path):
    """`keel verify cert.json --key <hex>` — the auditor's actual command."""
    path = tmp_path / "cert.json"
    path.write_text(json.dumps(bundle))
    env = {**os.environ, "PYTHONPATH": os.path.dirname(os.path.dirname(__file__))}

    ok = subprocess.run(
        [sys.executable, "-m", "keel.cli", "verify", str(path), "--key", PUB],
        capture_output=True, text=True, env=env)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "VALID" in ok.stdout

    tampered = json.loads(json.dumps(bundle))
    tampered["certificate"]["verdict"] = "REFUTED"
    bad_path = tmp_path / "tampered.json"
    bad_path.write_text(json.dumps(tampered))
    bad = subprocess.run(
        [sys.executable, "-m", "keel.cli", "verify", str(bad_path), "--key", PUB],
        capture_output=True, text=True, env=env)
    assert bad.returncode == 1, "tampered certificate must exit non-zero"
    assert "NOT VALID" in bad.stdout


def test_cli_refuses_to_run_keyless(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"certificate": {"signature": "00"}}))
    env = {k: v for k, v in os.environ.items() if k != "KEEL_PUBLIC_KEY"}
    env["PYTHONPATH"] = os.path.dirname(os.path.dirname(__file__))
    r = subprocess.run([sys.executable, "-m", "keel.cli", "verify", str(path)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 2
    assert "no public key" in r.stderr
