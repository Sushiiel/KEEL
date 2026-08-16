"""Payment confirmation must not be forgeable or replayable.

Razorpay signs `payment_link_id|reference_id|status|payment_id`. Anything
outside that envelope is attacker-controlled. These tests pin the two ways
that used to let one real payment become many licences.
"""
from __future__ import annotations

import hashlib
import hmac

import pytest

from keel import billing

SECRET = "rzp_test_secret_for_unit_tests"
VICTIM = "acct_1111111111111111"
ATTACKER = "acct_2222222222222222"


def _signed(account: str, pid: str, secret: str = SECRET,
            status: str = "paid") -> dict:
    """A callback exactly as Razorpay would produce it for `account`."""
    plink = "plink_TEST123"
    ref = f"{account}.abcdef123456"          # reference_id carries the account
    sig = hmac.new(secret.encode(), f"{plink}|{ref}|{status}|{pid}".encode(),
                   hashlib.sha256).hexdigest()
    return {"provider": "razorpay", "razorpay_payment_link_id": plink,
            "razorpay_payment_link_reference_id": ref,
            "razorpay_payment_link_status": status,
            "razorpay_payment_id": pid, "razorpay_signature": sig}


@pytest.fixture(autouse=True)
def _razorpay_env(monkeypatch, tmp_path):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_key")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", SECRET)
    yield
    for acct in (VICTIM, ATTACKER):
        try:
            billing.deactivate(acct)
        except Exception:
            pass


def test_a_valid_payment_activates_its_own_account():
    res = billing.confirm_checkout(_signed(VICTIM, "pay_AAA"),
                                   session_account=VICTIM)
    assert res["activated"] is True
    assert billing.status(VICTIM)["plan"] == "team"


def test_payment_cannot_be_redirected_to_another_account():
    """The original bug: the account rode in an UNSIGNED query param, so an
    attacker could take any valid callback and point it at any account."""
    forged = _signed(VICTIM, "pay_BBB")
    forged["account"] = ATTACKER              # the field that used to win
    res = billing.confirm_checkout(forged, session_account=ATTACKER)
    assert res["activated"] is False
    assert billing.status(ATTACKER)["plan"] != "team"


def test_one_payment_cannot_be_redeemed_twice():
    """A signature stays valid forever, so without a replay guard a single
    ₹830 payment renews the licence indefinitely."""
    first = billing.confirm_checkout(_signed(VICTIM, "pay_CCC"),
                                     session_account=VICTIM)
    assert first["activated"] is True
    second = billing.confirm_checkout(_signed(VICTIM, "pay_CCC"),
                                      session_account=VICTIM)
    assert second["activated"] is False
    assert "already redeemed" in second["error"]


def test_forged_signature_is_rejected():
    res = billing.confirm_checkout(_signed(VICTIM, "pay_DDD", secret="wrong-secret"),
                                   session_account=VICTIM)
    assert res["activated"] is False
    assert billing.status(VICTIM)["plan"] != "team"


def test_unpaid_status_is_rejected():
    res = billing.confirm_checkout(_signed(VICTIM, "pay_EEE", status="created"),
                                   session_account=VICTIM)
    assert res["activated"] is False


def test_payment_with_no_account_reference_is_rejected():
    """A link created outside our flow has no account in reference_id; we
    must refuse rather than silently crediting a default account."""
    params = _signed(VICTIM, "pay_FFF")
    plink, pid, status = "plink_TEST123", "pay_FFF", "paid"
    params["razorpay_payment_link_reference_id"] = "some-unrelated-ref"
    params["razorpay_signature"] = hmac.new(
        SECRET.encode(), f"{plink}|some-unrelated-ref|{status}|{pid}".encode(),
        hashlib.sha256).hexdigest()
    res = billing.confirm_checkout(params, session_account=VICTIM)
    assert res["activated"] is False


def test_missing_secret_never_activates(monkeypatch):
    """Belt and braces: with no secret configured, an empty signature must
    not compare equal to an empty expectation."""
    monkeypatch.delenv("RAZORPAY_KEY_SECRET", raising=False)
    params = _signed(VICTIM, "pay_GGG")
    params["razorpay_signature"] = ""
    assert billing.confirm_checkout(params, session_account=VICTIM)["activated"] is False


def test_checkout_link_puts_the_account_inside_the_signed_field(monkeypatch):
    """create_checkout must set reference_id, or confirm can't bind securely."""
    captured = {}

    def fake_request(pathpart, body):
        captured.update(body)
        return {"short_url": "https://rzp.io/i/test", "id": "plink_X"}

    monkeypatch.setattr(billing, "_razorpay_request", fake_request)
    billing.create_checkout("https://keel.best", account=VICTIM)
    assert captured["reference_id"].startswith(VICTIM + ".")
    assert "account=" not in captured["callback_url"]   # no unsigned account
