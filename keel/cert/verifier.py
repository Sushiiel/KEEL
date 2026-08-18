"""Offline certificate verification — the other half of the trust story.

A signed certificate is only worth something if someone who does NOT trust the
issuing server can check it. This module is that check: it takes a certificate
as plain JSON plus the authority's public key, and verifies everything a
relying party (auditor, insurer, counterparty, court) needs:

  1. the Ed25519 signature over the canonical payload
  2. that the payload hashes to the claimed transparency-log leaf
  3. that the leaf's Merkle inclusion proof reaches the claimed root

It deliberately imports no server, store, or config code — only hashlib, json,
and the Ed25519 primitive — so it runs anywhere the JSON does: a laptop with
`pip install keel`, a CI job, an auditor's air-gapped machine. The KEEL server
being down, compromised, or gone does not change the answer.

The canonicalization here must match authority.canonical_payload exactly:
sorted keys, compact separators, with signature/log_index/log_root excluded.
That contract is pinned by tests that verify a live-issued certificate through
this module.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# Must stay in lockstep with authority._UNSIGNED_FIELDS. Duplicated (rather
# than imported) so this module stays free of the authority's key-loading
# machinery — an offline verifier must never touch a private key.
UNSIGNED_FIELDS = ("signature", "log_index", "log_root")


def canonical_payload(cert: dict[str, Any]) -> bytes:
    """The exact bytes the authority signed, recomputed from plain JSON."""
    data = {k: v for k, v in cert.items() if k not in UNSIGNED_FIELDS}
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def leaf_hash(payload: bytes) -> str:
    """RFC 6962-style leaf hash (0x00 domain separator)."""
    return hashlib.sha256(b"\x00" + payload).hexdigest()


def _node_hash(left: str, right: str) -> str:
    return hashlib.sha256(
        b"\x01" + bytes.fromhex(left) + bytes.fromhex(right)).hexdigest()


def verify_signature(cert: dict[str, Any], public_key_hex: str) -> bool:
    """Does the authority's signature cover this exact certificate content?"""
    sig = cert.get("signature", "")
    if not sig or not public_key_hex:
        return False
    try:
        Ed25519PublicKey.from_public_bytes(
            bytes.fromhex(public_key_hex)).verify(
            bytes.fromhex(sig), canonical_payload(cert))
        return True
    except Exception:
        return False


def verify_inclusion(leaf: str, path: list[dict[str, str]], root: str) -> bool:
    """Walk a Merkle inclusion path from a leaf to the claimed root."""
    try:
        acc = leaf
        for step in path:
            if step["side"] == "left":
                acc = _node_hash(step["hash"], acc)
            else:
                acc = _node_hash(acc, step["hash"])
        return acc == root
    except Exception:
        return False


def verify(bundle: dict[str, Any], public_key_hex: str,
           expected_root: str | None = None) -> dict[str, Any]:
    """Verify a certificate bundle offline. Returns a full report, never raises.

    `bundle` may be a bare certificate dict, or any of the wrapped shapes KEEL
    emits (the /api/certificates response, an audit-pack sample). The overall
    `valid` is the conjunction of every check that COULD run; a check that
    could not run (no proof supplied) is reported as absent, not passed —
    absence of evidence must never read as evidence.

    `expected_root` pins the log root to one you obtained out-of-band (a
    published checkpoint, a prior export). Without it, inclusion proves the
    certificate is in *a* consistent log, not in the log you saw yesterday.
    """
    cert = bundle.get("certificate", bundle)
    proof = bundle.get("inclusion_proof")
    report: dict[str, Any] = {
        "cert_id": cert.get("cert_id", ""),
        "signer": cert.get("signer", ""),
        "checks": {},
        "valid": False,
    }
    if not isinstance(cert, dict) or not cert.get("signature"):
        report["error"] = "no certificate with a signature found in input"
        return report

    payload = canonical_payload(cert)
    computed_leaf = leaf_hash(payload)
    report["payload_sha256"] = computed_leaf

    sig_ok = verify_signature(cert, public_key_hex)
    report["checks"]["signature"] = sig_ok

    inc_ok = None
    root_ok = None
    if isinstance(proof, dict) and proof.get("path") is not None:
        # the leaf in the proof must be THIS certificate's payload — otherwise
        # a valid proof for some other entry would pass
        leaf_matches = proof.get("leaf") == computed_leaf
        report["checks"]["leaf_matches_payload"] = leaf_matches
        inc_ok = leaf_matches and verify_inclusion(
            computed_leaf, proof.get("path", []), proof.get("root", ""))
        report["checks"]["inclusion_proof"] = inc_ok
        report["log"] = {"index": proof.get("index"), "size": proof.get("size"),
                         "root": proof.get("root")}
        if expected_root is not None:
            root_ok = proof.get("root") == expected_root
            report["checks"]["root_pinned"] = root_ok
    else:
        report["checks"]["inclusion_proof"] = None    # not supplied — not passed

    ran = [v for v in report["checks"].values() if v is not None]
    report["valid"] = bool(ran) and all(ran) and sig_ok
    return report
