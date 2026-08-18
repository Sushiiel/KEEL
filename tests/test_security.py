"""Security regressions from the commit review: no SSRF, no LFI, no
cross-account authorization holes, no anonymous API surface.

The paywall-bypass tests that used to live here (dev-unlock in production,
forged licence signatures) are gone with the paywall itself — there is no
licence to forge and nothing to bypass. What replaced them is the check below
that a feature gate cannot be tricked into passing on a name KEEL does not
ship, since that is the failure mode a free build still has.
"""
import os
os.environ.setdefault("KEEL_SANDBOX", "1")
import tempfile
os.environ["KEEL_DATA_DIR"] = tempfile.mkdtemp(prefix="keel-sec-")

from keel import billing, accounts


def test_feature_gate_refuses_unknown_names():
    """require_feature() raises on a False result, so has_feature must not be
    permissive by default — otherwise a typo'd gate silently passes and an
    endpoint ships unprotected, which is exactly how ~34 routes once shipped
    anonymous."""
    assert not billing.has_feature("acct_x", "hsm_keyz")
    assert not billing.has_feature("acct_x", "")
    for real in billing.ALL_FEATURES:
        assert billing.has_feature("acct_x", real), real


def test_document_parser_refuses_untrusted_paths_and_urls():
    """The doc parser must never read arbitrary files or fetch URLs."""
    from keel.integrations.adapters import _safe_local_path
    os.environ.pop("KEEL_EVIDENCE_DIR", None)
    # no sandbox configured → nothing is readable
    assert _safe_local_path("/etc/passwd") is None
    assert _safe_local_path("http://169.254.169.254/") is None
    # with a sandbox, only paths inside it resolve; traversal + URLs rejected
    sandbox = tempfile.mkdtemp(prefix="keel-ev-")
    os.environ["KEEL_EVIDENCE_DIR"] = sandbox
    assert _safe_local_path("/etc/passwd") is None            # absolute outside
    assert _safe_local_path("../../etc/passwd") is None       # traversal
    assert _safe_local_path("https://evil.com/x") is None     # URL / SSRF
    assert _safe_local_path("report.pdf") is not None         # inside sandbox OK
    os.environ.pop("KEEL_EVIDENCE_DIR", None)


def test_grounding_check_never_reads_evidence_source():
    """A malicious evidence.source must not be read during grounding."""
    from keel.gateway.checkers import check_grounding
    from keel.gateway.models import ActionRequest, ActionClassSpec, Claim, EvidenceItem
    req = ActionRequest(
        agent_id="x", action_class="a", requires_evidence=False,
        claims=[Claim(statement="see the secret 12345", evidence_refs=["e1"])],
        evidence=[EvidenceItem(ref="e1", content="", source="/etc/passwd")])
    spec = ActionClassSpec(name="a", risk="low")
    res = check_grounding(req, spec)
    # the empty content means the number can't be grounded → fail, and crucially
    # /etc/passwd was never read (content stays empty)
    assert req.evidence[0].content == ""
    assert res.verdict == "fail"


def test_no_bare_json_404_for_browsers():
    """A person must never see a raw {"detail":"Not Found"} — regression guard."""
    from fastapi.testclient import TestClient
    from keel.server.app import app
    c = TestClient(app)
    # browser path → real HTML 404 page
    r = c.get("/some-typo")
    assert r.status_code == 404 and "text/html" in r.headers["content-type"]
    assert "doesn't exist" in r.text
    # API path → machine-readable JSON, NOT an HTML page.
    # When auth is required an anonymous caller gets 401 instead, deliberately:
    # a stranger must not be able to probe which /api paths exist. Both answers
    # are correct, so accept either and assert the shape of each.
    r = c.get("/api/nope")
    if r.status_code == 401:
        assert "authentication required" in r.json()["error"]
    else:
        assert r.status_code == 404 and r.json()["error"] == "not found"
    # favicon exists (browsers request it on every page load)
    assert c.get("/favicon.ico").status_code == 200
    # common typed paths redirect instead of 404ing
    for path, dest in [("/pricing", "/#pricing"), ("/login", "/app"),
                       ("/documentation", "/docs"), ("/upgrade", "/app#/account")]:
        r = c.get(path, follow_redirects=False)
        assert r.status_code == 307 and r.headers["location"] == dest, path
