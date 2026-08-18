"""Entitlements: everything is free, and the gate cannot silently mislead.

There is no paywall left to test. What still needs pinning is that the
entitlement API is unconditional and honest — because the failure mode of a
"free" build is the opposite of a paywall's: a feature check that quietly
returns False, or a UI that still advertises a price.
"""
import os
import tempfile

os.environ.setdefault("KEEL_SANDBOX", "1")
os.environ.setdefault("KEEL_DATA_DIR", tempfile.mkdtemp(prefix="keel-bill-"))

from keel import billing

ACC = "acct_test"
OTHER = "acct_someone_else"


def test_every_feature_is_available():
    for f in billing.ALL_FEATURES:
        assert billing.has_feature(ACC, f), f


def test_features_do_not_depend_on_the_account():
    """No account is privileged, and an unknown account is not penalised."""
    for acct in (ACC, OTHER, "", "acct_never_seen_before"):
        assert billing.entitlement(acct)["features"] == sorted(billing.ALL_FEATURES)


def test_unknown_feature_is_false():
    """The one thing has_feature must still refuse.

    require_feature() raises on a False result, so returning True for anything
    would turn a typo'd gate into a silently passing one — the bug class that
    let endpoints ship unprotected in the first place.
    """
    assert not billing.has_feature(ACC, "hsm_keyz")
    assert not billing.has_feature(ACC, "")
    assert not billing.has_feature(ACC, "arbitrary_new_thing")


def test_entitlement_never_expires():
    ent = billing.entitlement(ACC)
    assert ent["valid"] is True
    assert ent["expires_at"] is None, "a free plan must not carry an expiry"
    assert ent["plan"] == "free"


def test_no_payment_provider_is_configured():
    assert billing.provider() == "free"
    assert billing.status(ACC)["payments_live"] is False


def test_price_is_zero_and_not_periodic():
    pr = billing.price()
    assert pr["amount"] == 0
    assert pr["display"] == "Free"
    assert pr["period"] == "forever", "a free plan must not imply a billing period"
    assert billing.status(ACC)["price_usd"] == 0


def test_status_locks_nothing():
    st = billing.status(ACC)
    assert st["locked_features"] == []
    assert sorted(st["all_features"]) == sorted(billing.ALL_FEATURES)


def test_every_plan_name_maps_to_the_full_feature_set():
    """Legacy callers may still ask by plan name; none may get less."""
    for plan, feats in billing.FEATURES.items():
        assert set(feats) == set(billing.ALL_FEATURES), plan


def test_payment_surface_is_gone():
    """A leftover callable is a live paywall waiting to be re-wired, and a
    Razorpay/Stripe entry point we would no longer be maintaining."""
    for name in ("create_checkout", "confirm_checkout", "handle_webhook",
                 "handle_razorpay_webhook", "dev_activate", "activate",
                 "deactivate", "_claim_payment", "_razorpay_request",
                 "PRICE_CENTS", "PRICE_USD", "PRICE_INR", "PLAN_DAYS"):
        assert not hasattr(billing, name), f"billing.{name} still exists"


def test_no_price_string_leaks_from_the_api():
    """Guards the bug that understated a weekly price by 4.3x: nothing in the
    status payload may read like a charge."""
    blob = repr(billing.status(ACC)).lower()
    for bad in ("$10", "10.0", "830", "/mo", "month", "week", "razorpay",
                "stripe", "upgrade"):
        assert bad not in blob, f"status() leaks {bad!r}"
