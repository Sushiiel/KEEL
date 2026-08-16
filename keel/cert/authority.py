"""Certificate authority: canonicalization, Ed25519 signing, verification.

The signature covers the canonical JSON payload (sorted keys, no whitespace,
signature/log fields excluded). Non-repudiable and tamper-evident together
with the transparency log.
"""
from __future__ import annotations

import base64
import json
import os
import time
import uuid

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey)

from ..config import KEY_PATH, SIGNER_ID, TENANT
from ..models import Certificate
from ..store import Store
from . import translog

_UNSIGNED_FIELDS = {"signature", "log_index", "log_root"}


class SigningKeyError(RuntimeError):
    """KEEL_SIGNING_KEY_PEM was supplied but could not be used."""


def _key_from_env() -> Ed25519PrivateKey | None:
    """Load the authority key from KEEL_SIGNING_KEY_PEM.

    This is the durable option: on an ephemeral filesystem (Render, Fly,
    Heroku, a fresh container) a file-backed key is regenerated on every
    deploy, which silently invalidates every certificate, the transparency
    root, and every signed licence. An env-var key survives all of that.

    Accepts the PEM verbatim, with literal '\\n' escapes (what most dashboards
    produce when you paste a multi-line value), or base64 of the whole PEM.
    Generate one with `keel keygen`.
    """
    raw = os.environ.get("KEEL_SIGNING_KEY_PEM", "").strip()
    if not raw:
        return None
    candidates = [raw, raw.replace("\\n", "\n")]
    try:                                    # dashboards that reject newlines
        candidates.append(base64.b64decode(raw, validate=True).decode())
    except Exception:
        pass
    for text in candidates:
        try:
            key = serialization.load_pem_private_key(text.encode(), password=None)
        except Exception:
            continue
        if not isinstance(key, Ed25519PrivateKey):
            raise SigningKeyError(
                f"KEEL_SIGNING_KEY_PEM is a {type(key).__name__}; KEEL certificates "
                "require Ed25519. Generate one with `keel keygen`.")
        return key
    # Fail loudly. Silently falling back to a fresh key is the exact failure
    # this variable exists to prevent — it would look healthy while breaking
    # verification for every certificate ever issued.
    raise SigningKeyError(
        "KEEL_SIGNING_KEY_PEM is set but is not a readable PEM private key. "
        "Generate one with `keel keygen` and paste the whole block, "
        "-----BEGIN PRIVATE KEY----- line included.")


def _load_or_create_key() -> Ed25519PrivateKey:
    env_key = _key_from_env()
    if env_key is not None:
        return env_key
    if KEY_PATH.exists():
        return serialization.load_pem_private_key(KEY_PATH.read_bytes(),
                                                  password=None)  # type: ignore[return-value]
    key = Ed25519PrivateKey.generate()
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    KEY_PATH.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    try:
        KEY_PATH.chmod(0o600)               # private key: owner-only
    except OSError:
        pass
    return key


def export_private_pem(key: Ed25519PrivateKey | None = None) -> str:
    """PEM text for an Ed25519 private key — what `keel keygen` prints."""
    key = key or Ed25519PrivateKey.generate()
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()


_KEY: Ed25519PrivateKey | None = None


def signing_key() -> Ed25519PrivateKey:
    global _KEY
    if _KEY is None:
        _KEY = _load_or_create_key()
    return _KEY


def public_key_hex() -> str:
    pub = signing_key().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return pub.hex()


def canonical_payload(cert: Certificate) -> bytes:
    data = cert.model_dump()
    for f in _UNSIGNED_FIELDS:
        data.pop(f, None)
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


def new_cert_id() -> str:
    return f"keel:cert:{uuid.uuid4().hex[:16].upper()}"


def issue(store: Store, cert: Certificate) -> Certificate:
    """Sign, log, persist. The certificate becomes the product artifact."""
    cert.cert_id = cert.cert_id or new_cert_id()
    cert.tenant = cert.tenant or TENANT
    cert.created_at = cert.created_at or time.time()
    cert.signer = SIGNER_ID
    payload = canonical_payload(cert)
    cert.signature = signing_key().sign(payload).hex()
    idx, root = translog.append(store, payload, cert.cert_id, cert.created_at)
    cert.log_index, cert.log_root = idx, root
    store.put_certificate(cert)
    return cert


def verify(cert: Certificate) -> dict:
    payload = canonical_payload(cert)
    pub_raw = signing_key().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    pub = Ed25519PublicKey.from_public_bytes(pub_raw)
    try:
        pub.verify(bytes.fromhex(cert.signature), payload)
        sig_ok = True
    except Exception:
        sig_ok = False
    return {"signature_valid": sig_ok, "signer": cert.signer,
            "public_key": public_key_hex(),
            "payload_sha256": translog.leaf_hash(payload)}
