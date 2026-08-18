"""Brute-force protection, exercised through the real HTTP surface.

The unit tests in test_ratelimit.py prove the limiter's arithmetic. These prove
it is actually WIRED IN — the failure mode being a correct limiter that no
route ever calls.
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("KEEL_SANDBOX", "1")
os.environ.setdefault("KEEL_DATA_DIR", tempfile.mkdtemp(prefix="keel-rl-"))

import pytest
from fastapi.testclient import TestClient

from keel import accounts, ratelimit
from keel.server.app import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(ratelimit, "_DISABLED", False)
    ratelimit.reset()
    yield
    ratelimit.reset()


def test_password_guessing_is_throttled():
    limit, _ = ratelimit.BUDGETS["login"]
    seen = [client.post("/api/auth/login",
                        json={"email": "victim@example.com", "password": f"guess{i}"}
                        ).status_code
            for i in range(limit + 4)]
    assert 429 in seen, f"login was never throttled: {seen}"
    assert seen.index(429) <= limit, "throttling kicked in later than the budget"


def test_throttled_response_says_when_to_retry():
    limit, _ = ratelimit.BUDGETS["login"]
    r = None
    for i in range(limit + 3):
        r = client.post("/api/auth/login",
                        json={"email": "v2@example.com", "password": "nope"})
        if r.status_code == 429:
            break
    assert r.status_code == 429
    detail = r.json()["detail"]
    assert detail["retry_after_seconds"] >= 1


def test_one_account_being_attacked_does_not_lock_out_another():
    """A per-account limit must not become a denial-of-service against
    everyone else on the deployment."""
    limit, _ = ratelimit.BUDGETS["login"]
    for i in range(limit + 5):
        client.post("/api/auth/login",
                    json={"email": "target@example.com", "password": f"g{i}"})
    r = client.post("/api/auth/login",
                    json={"email": "bystander@example.com", "password": "wrong"})
    assert r.status_code == 401, "bystander was rate-limited, not just rejected"


def test_a_correct_password_still_works_after_a_few_typos():
    """The limiter must not lock a legitimate user out of their own account."""
    email = "realuser@example.com"
    accounts.create_account(email, "correct-horse-battery")
    for _ in range(3):
        assert client.post("/api/auth/login",
                           json={"email": email, "password": "typo"}
                           ).status_code == 401
    r = client.post("/api/auth/login",
                    json={"email": email, "password": "correct-horse-battery"})
    assert r.status_code == 200, "legitimate sign-in was blocked"


def test_successful_login_clears_the_budget():
    email = "resetuser@example.com"
    accounts.create_account(email, "correct-horse-battery")
    for _ in range(4):
        client.post("/api/auth/login", json={"email": email, "password": "typo"})
    assert client.post("/api/auth/login",
                       json={"email": email, "password": "correct-horse-battery"}
                       ).status_code == 200
    # budget cleared, so a full run of attempts is available again
    limit, _ = ratelimit.BUDGETS["login"]
    codes = [client.post("/api/auth/login",
                         json={"email": email, "password": "typo"}).status_code
             for _ in range(limit)]
    assert 429 not in codes, "budget was not reset on success"


def test_signup_flooding_is_throttled(monkeypatch):
    monkeypatch.setenv("KEEL_SIGNUP", "open")
    limit, _ = ratelimit.BUDGETS["signup"]
    codes = [client.post("/api/auth/signup",
                         json={"email": f"flood{i}@example.com",
                               "password": "password12345"}).status_code
             for i in range(limit + 3)]
    assert 429 in codes, f"signup was never throttled: {codes}"


def test_email_case_does_not_create_a_fresh_budget():
    """Normalisation matters: otherwise VICTIM@x.com and victim@x.com are two
    budgets and the limit is trivially doubled."""
    limit, _ = ratelimit.BUDGETS["login"]
    for i in range(limit):
        client.post("/api/auth/login",
                    json={"email": "Case@Example.com", "password": f"g{i}"})
    r = client.post("/api/auth/login",
                    json={"email": "case@example.com", "password": "again"})
    assert r.status_code == 429, "changing capitalisation bypassed the limit"
