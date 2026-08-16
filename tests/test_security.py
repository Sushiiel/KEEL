"""Security regressions from the commit review: no paywall bypass in prod,
no SSRF, no cross-account authorization holes."""
import os
os.environ.setdefault("KEEL_SANDBOX", "1")
import tempfile
os.environ["KEEL_DATA_DIR"] = tempfile.mkdtemp(prefix="keel-sec-")

from keel import billing, accounts


def test_dev_unlock_disabled_in_production():
    """dev activation must NOT bypass the paywall when auth is required and no
    explicit unlock code is configured."""
    os.environ["KEEL_AUTH_REQUIRED"] = "1"
    os.environ.pop("KEEL_UNLOCK_CODE", None)
    billing.deactivate("acct_x")
    res = billing.dev_activate("acct_x", "DEV-UNLOCK")
    assert not res["activated"], "prod dev-unlock must be refused"
    assert not billing.has_feature("acct_x", "hsm_keys")
    os.environ["KEEL_AUTH_REQUIRED"] = "0"


def test_dev_unlock_works_locally():
    os.environ["KEEL_AUTH_REQUIRED"] = "0"
    billing.deactivate("acct_y")
    assert billing.dev_activate("acct_y", "DEV-UNLOCK")["activated"]


def test_explicit_unlock_code_required_when_set():
    os.environ["KEEL_AUTH_REQUIRED"] = "1"
    os.environ["KEEL_UNLOCK_CODE"] = "s3cret-code"
    billing.deactivate("acct_z")
    assert not billing.dev_activate("acct_z", "DEV-UNLOCK")["activated"]  # default rejected
    assert billing.dev_activate("acct_z", "s3cret-code")["activated"]     # real code works
    os.environ["KEEL_AUTH_REQUIRED"] = "0"
    os.environ.pop("KEEL_UNLOCK_CODE", None)


def test_entitlement_fails_closed_on_corrupt_license():
    billing._bill_store().kv_set(billing._lkey("acct_bad"), {"plan": "team", "signature": "00"})
    assert not billing.has_feature("acct_bad", "hsm_keys")   # bad signature → free


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
