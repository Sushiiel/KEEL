"""The authority signing key must survive a redeploy.

KEEL's entire value proposition is that a certificate issued yesterday still
verifies today. On an ephemeral filesystem a file-backed key is regenerated on
every deploy, which silently breaks that — and also invalidates every signed
licence. KEEL_SIGNING_KEY_PEM is the durable path; these tests pin its
contract, including that a bad value fails loudly instead of quietly minting a
fresh key.
"""
from __future__ import annotations

import base64
import importlib
import os

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from keel.cert import authority
from keel.cert.authority import SigningKeyError, export_private_pem


def _reload_with(monkeypatch, value: str | None):
    """Re-resolve the key with KEEL_SIGNING_KEY_PEM set to `value`."""
    if value is None:
        monkeypatch.delenv("KEEL_SIGNING_KEY_PEM", raising=False)
    else:
        monkeypatch.setenv("KEEL_SIGNING_KEY_PEM", value)
    authority._KEY = None                      # drop the process-wide cache
    return authority.signing_key()


@pytest.fixture(autouse=True)
def _restore_cached_key():
    yield
    authority._KEY = None                      # never leak a test key


def test_env_key_is_used_verbatim(monkeypatch):
    pem = export_private_pem()
    key = _reload_with(monkeypatch, pem)
    assert isinstance(key, Ed25519PrivateKey)
    assert export_private_pem(key) == pem


def test_key_is_stable_across_restarts(monkeypatch):
    """The property that actually matters: same env var -> same public key."""
    pem = export_private_pem()
    _reload_with(monkeypatch, pem)
    pub_a = authority.public_key_hex()
    _reload_with(monkeypatch, pem)             # simulate a fresh process
    assert authority.public_key_hex() == pub_a


def test_certificate_still_verifies_after_a_restart(monkeypatch):
    """End-to-end: sign with one process, verify with the next."""
    pem = export_private_pem()
    _reload_with(monkeypatch, pem)
    payload = b"decision-payload"
    sig = authority.signing_key().sign(payload)

    _reload_with(monkeypatch, pem)             # "redeploy"
    authority.signing_key().public_key().verify(sig, payload)   # must not raise


def test_escaped_newlines_are_accepted(monkeypatch):
    """Most dashboards turn a pasted multi-line secret into \\n escapes."""
    pem = export_private_pem()
    key = _reload_with(monkeypatch, pem.replace("\n", "\\n"))
    assert export_private_pem(key) == pem


def test_base64_wrapped_pem_is_accepted(monkeypatch):
    pem = export_private_pem()
    key = _reload_with(monkeypatch, base64.b64encode(pem.encode()).decode())
    assert export_private_pem(key) == pem


def test_garbage_fails_loudly_instead_of_minting_a_new_key(monkeypatch):
    """The critical safety property.

    Falling back to a fresh key here would look perfectly healthy while
    breaking verification for every certificate ever issued.
    """
    with pytest.raises(SigningKeyError):
        _reload_with(monkeypatch, "not-a-pem")


def test_wrong_algorithm_is_rejected(monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    rsa_pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()
    with pytest.raises(SigningKeyError):
        _reload_with(monkeypatch, rsa_pem)


def test_unset_falls_back_to_the_file(monkeypatch):
    """Local dev and self-hosting keep working with no configuration."""
    key = _reload_with(monkeypatch, None)
    assert isinstance(key, Ed25519PrivateKey)


def test_keygen_cli_emits_a_usable_key(capsys):
    from keel.cli import main
    assert main(["keygen", "--quiet"]) == 0
    pem = capsys.readouterr().out
    assert "BEGIN PRIVATE KEY" in pem
    from cryptography.hazmat.primitives import serialization
    assert isinstance(
        serialization.load_pem_private_key(pem.encode(), password=None),
        Ed25519PrivateKey)
