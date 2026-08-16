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
