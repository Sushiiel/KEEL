"""The $10 Team paywall: features gate on entitlement, license signed, dev+stripe paths."""
import os
os.environ.setdefault("KEEL_SANDBOX", "1")
import tempfile
os.environ.setdefault("KEEL_DATA_DIR", tempfile.mkdtemp(prefix="keel-bill-"))

from keel import billing


ACC = "acct_test"


def setup_function():
    billing.deactivate(ACC)


def test_free_by_default():
    ent = billing.entitlement(ACC)
    assert ent["plan"] == "free" and not ent["valid"]
    assert not billing.has_feature(ACC, "hsm_keys")
    assert not billing.has_feature(ACC, "approval_integrations")


def test_activate_unlocks_all_team_features():
    billing.activate(ACC, "team", source="test")
    for f in billing.FEATURES["team"]:
        assert billing.has_feature(ACC, f), f
    assert billing.entitlement(ACC)["plan"] == "team"


def test_license_is_signed_and_tamper_evident():
    billing.activate(ACC, "team", source="test")
    lic = billing._bill_store().kv_get(billing._lkey(ACC))
    assert billing._verify(lic)
    lic["plan"] = "enterprise"                 # tamper
    assert not billing._verify(lic)


def test_expired_license_is_invalid():
    billing.activate(ACC, "team", source="test", days=-1)   # already expired
    assert not billing.entitlement(ACC)["valid"]
    assert not billing.has_feature(ACC, "hsm_keys")


def test_dev_activate_requires_code():
    assert not billing.dev_activate(ACC, "wrong")["activated"]
    assert billing.dev_activate(ACC, os.environ.get("KEEL_UNLOCK_CODE", "DEV-UNLOCK"))["activated"]


def test_price_is_ten_dollars():
    assert billing.PRICE_USD == 10.0
    assert billing.status(ACC)["price_usd"] == 10.0
