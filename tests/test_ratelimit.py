"""Rate limiting must actually stop a brute-force, and must not lock out
legitimate users.

Both halves matter. A limiter that never triggers is decoration; one that
triggers on normal use is an outage.
"""
from __future__ import annotations

import time

import pytest

from keel import ratelimit


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(ratelimit, "_DISABLED", False)
    ratelimit.reset()
    yield
    ratelimit.reset()


def test_allows_up_to_the_limit_then_blocks():
    limit, _ = ratelimit.BUDGETS["login"]
    for i in range(limit):
        allowed, retry = ratelimit.check("login", "user@example.com")
        assert allowed is True, f"blocked legitimate attempt {i + 1}/{limit}"
        assert retry == 0.0
    allowed, retry = ratelimit.check("login", "user@example.com")
    assert allowed is False
    assert retry > 0, "a blocked caller must be told when to retry"


def test_identities_are_independent():
    """One user exhausting their budget must not lock anyone else out."""
    limit, _ = ratelimit.BUDGETS["login"]
    for _ in range(limit + 3):
        ratelimit.check("login", "victim@example.com")
    assert ratelimit.check("login", "bystander@example.com")[0] is True


def test_budgets_are_independent():
    limit, _ = ratelimit.BUDGETS["signup"]
    for _ in range(limit + 2):
        ratelimit.check("signup", "1.2.3.4")
    assert ratelimit.check("signup", "1.2.3.4")[0] is False
    assert ratelimit.check("login", "1.2.3.4")[0] is True


def test_successful_login_clears_the_budget():
    """A user who mistypes twice then succeeds should not stay near the limit."""
    for _ in range(3):
        ratelimit.check("login", "user@example.com")
    ratelimit.reset("login", "user@example.com")
    limit, _ = ratelimit.BUDGETS["login"]
    for i in range(limit):
        assert ratelimit.check("login", "user@example.com")[0] is True, i


def test_window_expiry_restores_access(monkeypatch):
    """The block must be temporary, not permanent."""
    fake = [1000.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: fake[0])
    limit, window = ratelimit.BUDGETS["login"]
    for _ in range(limit):
        ratelimit.check("login", "user@example.com")
    assert ratelimit.check("login", "user@example.com")[0] is False
    fake[0] += window + 1
    assert ratelimit.check("login", "user@example.com")[0] is True


def test_unknown_budget_fails_open():
    """A typo'd budget name must not lock a user out of their own account."""
    assert ratelimit.check("no-such-budget", "someone")[0] is True


def test_disabled_by_env(monkeypatch):
    monkeypatch.setattr(ratelimit, "_DISABLED", True)
    for _ in range(1000):
        assert ratelimit.check("login", "user@example.com")[0] is True


def test_forwarded_for_ignored_unless_proxy_is_trusted(monkeypatch):
    """X-Forwarded-For is caller-supplied. Honouring it when not behind a proxy
    would let an attacker mint a fresh identity per request and bypass every
    limit."""
    headers = {"x-forwarded-for": "9.9.9.9"}
    monkeypatch.delenv("KEEL_TRUSTED_PROXY", raising=False)
    assert ratelimit.client_ip(headers, fallback="10.0.0.1") == "10.0.0.1"
    monkeypatch.setenv("KEEL_TRUSTED_PROXY", "1")
    assert ratelimit.client_ip(headers, fallback="10.0.0.1") == "9.9.9.9"


def test_forwarded_for_takes_the_leftmost_hop(monkeypatch):
    monkeypatch.setenv("KEEL_TRUSTED_PROXY", "1")
    headers = {"x-forwarded-for": "1.1.1.1, 2.2.2.2, 3.3.3.3"}
    assert ratelimit.client_ip(headers, fallback="") == "1.1.1.1"


def test_missing_ip_never_crashes(monkeypatch):
    monkeypatch.setenv("KEEL_TRUSTED_PROXY", "1")
    assert ratelimit.client_ip({}, fallback="") == "unknown"


def test_sweep_bounds_memory(monkeypatch):
    """One request per unique address must not grow state forever — that is a
    memory leak and a cheap remote resource attack."""
    fake = [1000.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: fake[0])
    for i in range(500):
        ratelimit.check("login", f"ip-{i}")
    assert ratelimit.state()["tracked_identities"] == 500
    widest = max(w for _, w in ratelimit.BUDGETS.values())
    fake[0] += widest + 120           # everything is now stale
    ratelimit.check("login", "someone-new")   # triggers a sweep
    assert ratelimit.state()["tracked_identities"] < 50


def test_state_is_honest_about_the_guarantee():
    """Never imply a cluster-wide limit we do not provide."""
    st = ratelimit.state()
    assert st["backend"] == "in-process"
    assert "not shared across replicas" in st["scope"].lower()
    assert set(st["budgets"]) == set(ratelimit.BUDGETS)
