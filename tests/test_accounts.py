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


def test_credentials_are_isolated_between_accounts():
    """Entitlement isolation is moot now that every feature is free — but
    IDENTITY isolation is the property that still has to hold, and it is the one
    that matters more. One account's API key must never resolve to another.
    """
    a1 = accounts.create_account("a1@y.com", "password123")
    a2 = accounts.create_account("a2@y.com", "password123")
    assert a1["account_id"] != a2["account_id"]
    assert a1["api_key"] != a2["api_key"]

    # each key resolves only to its own account
    assert accounts.account_by_api_key(a1["api_key"])["account_id"] == a1["account_id"]
    assert accounts.account_by_api_key(a2["api_key"])["account_id"] == a2["account_id"]

    # and each session token likewise
    s1 = accounts.issue_session(a1)
    assert accounts.account_from_session(s1)["account_id"] == a1["account_id"]
    assert accounts.resolve(session_token=s1, api_key="")["account_id"] != a2["account_id"]


def test_every_account_gets_the_full_feature_set():
    """The free model, stated as a test: no account is privileged."""
    a1 = accounts.create_account("f1@y.com", "password123")
    a2 = accounts.create_account("f2@y.com", "password123")
    for acct in (a1, a2):
        for feature in billing.ALL_FEATURES:
            assert billing.has_feature(acct["account_id"], feature), feature
