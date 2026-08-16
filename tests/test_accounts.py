"""Accounts, sessions, per-account isolation."""
import os
os.environ.setdefault("KEEL_SANDBOX", "1")
import tempfile
os.environ["KEEL_DATA_DIR"] = tempfile.mkdtemp(prefix="keel-acct-")

import pytest
from keel import accounts, billing


def test_signup_login_and_password_hashing():
    a = accounts.create_account("x@y.com", "password123", "X")
    assert a["account_id"].startswith("acct_") and a["api_key"].startswith("keel_ak_")
    assert accounts.authenticate("x@y.com", "password123")
    assert accounts.authenticate("x@y.com", "wrong") is None


def test_duplicate_and_weak_rejected():
    accounts.create_account("dup@y.com", "password123")
    with pytest.raises(ValueError):
        accounts.create_account("dup@y.com", "password123")
    with pytest.raises(ValueError):
        accounts.create_account("z@y.com", "short")


def test_session_roundtrip_and_tamper():
    a = accounts.account_by_email("x@y.com")
    tok = accounts.issue_session(a)
    assert accounts.account_from_session(tok)["email"] == "x@y.com"
    assert accounts.account_from_session(tok[:-2] + "00") is None   # bad sig


def test_api_key_resolves_account():
    a = accounts.create_account("k@y.com", "password123")
    assert accounts.account_by_api_key(a["api_key"])["email"] == "k@y.com"


def test_entitlement_isolation_between_accounts():
    a1 = accounts.create_account("a1@y.com", "password123")
    a2 = accounts.create_account("a2@y.com", "password123")
    billing.activate(a1["account_id"], "team", source="test")
    assert billing.has_feature(a1["account_id"], "hsm_keys")
    assert not billing.has_feature(a2["account_id"], "hsm_keys")   # isolated
