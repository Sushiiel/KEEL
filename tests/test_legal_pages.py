"""Policy pages must be public, complete, and honest about configuration.

A prospective user reads these before creating an account, so they must render
without a session. They must also never invent a legal entity: an unconfigured
deployment says so rather than naming a company that does not exist.

Note on env: this module must NOT set KEEL_AUTH_REQUIRED at import time. Doing
so leaked into every other test module that imports the app and broke
test_security.py, since the app reads the variable per request. Each test that
needs a hardened deployment sets it via monkeypatch instead, which pytest
unwinds afterwards.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from keel import legal
from keel.server.app import app

client = TestClient(app)
PAGES = ["/terms", "/privacy", "/refunds", "/contact"]


@pytest.mark.parametrize("path", PAGES)
def test_reachable_without_authentication(path, monkeypatch):
    """The property that matters: public even on a hardened deployment, where
    everything else under the API requires a session."""
    monkeypatch.setenv("KEEL_AUTH_REQUIRED", "1")
    r = client.get(path)
    assert r.status_code == 200, f"{path} returned {r.status_code}"
    assert "text/html" in r.headers["content-type"]


@pytest.mark.parametrize("path", PAGES)
def test_pages_cross_link_to_each_other(path):
    body = client.get(path).text
    for other in PAGES:
        assert f'href="{other}"' in body, f"{path} does not link to {other}"


def test_unconfigured_deployment_admits_it(monkeypatch):
    """The important honesty property: never name a company that isn't there."""
    for var in ("KEEL_LEGAL_ENTITY", "KEEL_LEGAL_ADDRESS", "KEEL_SUPPORT_EMAIL"):
        monkeypatch.delenv(var, raising=False)
    assert legal.is_configured() is False
    body = client.get("/terms").text
    assert "not yet in force" in body
    assert "KEEL_LEGAL_ENTITY" in body           # tells the operator the fix


def test_configured_deployment_shows_the_operator(monkeypatch):
    monkeypatch.setenv("KEEL_LEGAL_ENTITY", "Example Labs Pvt Ltd")
    monkeypatch.setenv("KEEL_LEGAL_ADDRESS", "1 Example Road, Chennai 600001")
    monkeypatch.setenv("KEEL_SUPPORT_EMAIL", "support@example.com")
    assert legal.is_configured() is True
    body = client.get("/contact").text
    assert "Example Labs Pvt Ltd" in body
    assert "support@example.com" in body
    assert "not yet in force" not in body


def test_operator_details_are_escaped(monkeypatch):
    """Operator config is trusted-ish, but it lands in HTML — escape it."""
    monkeypatch.setenv("KEEL_LEGAL_ENTITY", '<script>alert(1)</script>')
    monkeypatch.setenv("KEEL_LEGAL_ADDRESS", "1 Example Road")
    monkeypatch.setenv("KEEL_SUPPORT_EMAIL", "support@example.com")
    body = client.get("/contact").text
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_refunds_page_is_still_reachable_and_says_there_is_nothing_to_refund():
    """The page stays because the footer and four redirect aliases point at it.
    Asserted loosely on wording, strictly on the two facts a reader needs:
    nothing is charged, so nothing needs cancelling."""
    body = client.get("/refunds").text.lower()
    assert "free" in body
    assert "no payment" in body or "nothing to refund" in body
    for stale in ("$10", "830", "per week", "subscription renews"):
        assert stale not in body, f"/refunds still mentions {stale!r}"


def test_privacy_page_covers_the_required_disclosures():
    body = client.get("/privacy").text.lower()
    for topic in ("what we collect", "retention", "security", "your rights"):
        assert topic in body, f"privacy page is missing: {topic}"
    # No payments are taken at all, so the page must say no payment data is
    # collected. Kept loose so rewording doesn't break the test.
    assert "payment" in body
    assert "no payment" in body or "takes no payments" in body


def test_no_page_advertises_a_price():
    """A policy page quoting a price is worse than a stale marketing page —
    it reads as a term of the agreement.

    Matched on word boundaries so the liability cap (US$100) is not mistaken
    for the old $10 price.
    """
    import re
    patterns = [r"\$10\b", r"₹\s*830", r"/mo\b", r"\bper week\b", r"\bupgrade\b",
                r"\brazorpay\b", r"\bstripe\b", r"free for now",
                r"currently free"]
    for path in PAGES:
        body = client.get(path).text.lower()
        for pat in patterns:
            assert not re.search(pat, body), f"{path} matches {pat!r}"
        # "subscription" is allowed only where it is being denied
        for m in re.finditer(r"\bsubscription\b", body):
            context = body[max(0, m.start() - 40):m.start()]
            assert re.search(r"\bno\b|\bnot\b|never", context), \
                f"{path} refers to a subscription that does not exist"


def test_terms_states_a_meaningful_liability_cap():
    """'Limited to the amount you paid' is zero for a free service — misleading
    to the reader and unenforceable in several jurisdictions."""
    body = client.get("/terms").text.lower()
    assert "amount you paid" not in body
    assert "cannot lawfully be limited" in body


def test_aliases_redirect(monkeypatch):
    for alias, target in [("/privacy-policy", "/privacy"),
                          ("/terms-of-service", "/terms"),
                          ("/refund-policy", "/refunds"),
                          ("/contact-us", "/contact")]:
        r = client.get(alias, follow_redirects=False)
        assert r.status_code == 307, f"{alias} did not redirect"
        assert r.headers["location"] == target


def test_landing_page_footer_links_to_all_policies():
    body = client.get("/").text
    for path in PAGES:
        assert f'href="{path}"' in body, f"footer is missing a link to {path}"
