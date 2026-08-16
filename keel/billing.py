"""Billing & entitlements — the $10 Team paywall, end to end.

A license is a signed record (Ed25519, same authority key as certificates)
stored per deployment. It is created either by a real Stripe payment
(Checkout → webhook / confirm) or, for local self-host evaluation, by an
explicit activation code. Feature gates read the entitlement; unpaid
deployments get the free plan.

Trust model, stated honestly:
- Managed/cloud KEEL: the license is issued and verified server-side, so the
  paywall is enforced.
- Self-hosted OSS KEEL: the operator holds the signing key, so the gate is a
  license mechanism, not DRM — the paid value is managed hosting, hardened
  key custody, and support, which self-hosting doesn't provide anyway.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Optional

from .cert import authority
from .store import Store, get_store

PRICE_CENTS = 1000                     # $10 (Stripe / USD)
PRICE_USD = PRICE_CENTS / 100
PRICE_INR = float(os.environ.get("KEEL_PRICE_INR", "830"))   # ~$10 in ₹
PLAN_DAYS = 7                          # Team is weekly ($10 / ₹830 per week)


def provider() -> str:
    """Which payment rail is active. Razorpay is the India-first default when
    configured; Stripe otherwise; 'dev' when neither has keys."""
    p = os.environ.get("KEEL_PAYMENT_PROVIDER")
    if p:
        return p
    if os.environ.get("RAZORPAY_KEY_ID") and os.environ.get("RAZORPAY_KEY_SECRET"):
        return "razorpay"
    if os.environ.get("STRIPE_SECRET_KEY"):
        return "stripe"
    return "dev"


def price() -> dict[str, Any]:
    prov = provider()
    if prov == "razorpay":
        return {"amount": PRICE_INR, "currency": "INR",
                "display": f"₹{PRICE_INR:.0f}", "period": "week"}
    return {"amount": PRICE_USD, "currency": "USD",
            "display": f"${PRICE_USD:.0f}", "period": "week"}


_LICENSE_PREFIX = "billing_license:"
_USED_PAYMENTS = "billing_redeemed_payments"   # replay guard: spent payment ids


def _lkey(account_id: str) -> str:
    return _LICENSE_PREFIX + (account_id or "acct_default")

# feature → the plan that unlocks it
FEATURES: dict[str, set[str]] = {
    "free": set(),
    "team": {"managed_hosting", "hsm_keys", "approval_integrations",
             "evidence_export_full", "evidence_scheduling", "priority_support"},
    "enterprise": {"managed_hosting", "hsm_keys", "approval_integrations",
                   "evidence_export_full", "evidence_scheduling",
                   "priority_support", "sso", "rbac", "private_deploy",
                   "worm_retention", "custom_policy"},
}


def _bill_store() -> Store:
    return get_store("gateway")


# ── license lifecycle ────────────────────────────────────────────────────────

def _sign(record: dict[str, Any]) -> str:
    payload = json.dumps({k: v for k, v in record.items() if k != "signature"},
                         sort_keys=True, separators=(",", ":")).encode()
    return authority.signing_key().sign(payload).hex()


def _verify(record: dict[str, Any]) -> bool:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives import serialization
    payload = json.dumps({k: v for k, v in record.items() if k != "signature"},
                         sort_keys=True, separators=(",", ":")).encode()
    pub_raw = authority.signing_key().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    try:
        Ed25519PublicKey.from_public_bytes(pub_raw).verify(
            bytes.fromhex(record.get("signature", "")), payload)
        return True
    except Exception:
        return False


def activate(account_id: str, plan: str = "team", source: str = "manual",
             seats: int = 1, days: int = PLAN_DAYS,
             reference: str = "") -> dict[str, Any]:
    """Issue and store a signed license for a specific account."""
    now = time.time()
    record = {
        "account_id": account_id, "plan": plan, "seats": seats,
        "source": source, "reference": reference, "activated_at": now,
        "expires_at": now + days * 86400,
        "amount": price()["amount"] if plan == "team" else None,
        "currency": price()["currency"] if plan == "team" else None,
    }
    record["signature"] = _sign(record)
    _bill_store().kv_set(_lkey(account_id), record)
    return record


def entitlement(account_id: str = "acct_default") -> dict[str, Any]:
    """Current plan + feature set + validity for an account."""
    lic = _bill_store().kv_get(_lkey(account_id))
    if not lic or not _verify(lic) or lic.get("expires_at", 0) < time.time():
        return {"plan": "free", "features": sorted(FEATURES["free"]),
                "valid": False, "reason": "no active paid license"}
    return {"plan": lic["plan"],
            "features": sorted(FEATURES.get(lic["plan"], set())),
            "valid": True, "seats": lic.get("seats", 1),
            "expires_at": lic.get("expires_at"), "source": lic.get("source"),
            "amount": lic.get("amount"), "currency": lic.get("currency")}


def has_feature(account_id: str, feature: str) -> bool:
    return feature in set(entitlement(account_id)["features"])


def status(account_id: str = "acct_default") -> dict[str, Any]:
    ent = entitlement(account_id)
    pr = price()
    return {**ent, "provider": provider(), "price": pr,
            "price_display": pr["display"], "price_usd": PRICE_USD,
            "payments_live": provider() in ("razorpay", "stripe"),
            "all_team_features": sorted(FEATURES["team"]),
            "locked_features": sorted(set(FEATURES["team"]) - set(ent["features"]))}


# ── payment: Stripe Checkout (real) + local activation (dev) ─────────────────

def _razorpay_request(pathpart: str, body: dict) -> dict[str, Any]:
    """Call the Razorpay REST API with HTTP basic auth (no SDK needed)."""
    import base64, json as _json, urllib.request
    kid = os.environ["RAZORPAY_KEY_ID"]; ksec = os.environ["RAZORPAY_KEY_SECRET"]
    auth = base64.b64encode(f"{kid}:{ksec}".encode()).decode()
    req = urllib.request.Request(
        "https://api.razorpay.com/v1/" + pathpart,
        data=_json.dumps(body).encode(),
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return _json.loads(r.read())


def create_checkout(base_url: str, account: str = "acct_default") -> dict[str, Any]:
    """Start a Team checkout on the active provider (Razorpay in India, Stripe
    elsewhere). Returns a hosted payment URL to redirect to, or a dev path."""
    prov = provider()
    if prov == "razorpay":
        try:
            # The account is carried in reference_id because that field is
            # INSIDE Razorpay's callback HMAC (plink|ref|status|payment_id).
            # A query param is not, so it could be swapped after payment to
            # credit any account — see confirm_checkout.
            link = _razorpay_request("payment_links", {
                "amount": int(round(PRICE_INR * 100)),   # paise
                "currency": "INR",
                "description": "KEEL Team (weekly)",
                "reference_id": f"{account}.{uuid.uuid4().hex[:12]}",
                "notes": {"account": account, "plan": "team"},
                "callback_url": f"{base_url}/app#/billing?checkout=success&provider=razorpay",
                "callback_method": "get"})
            return {"mode": "razorpay", "url": link["short_url"], "id": link.get("id")}
        except Exception as e:
            return {"mode": "error", "error": f"razorpay error: {e}"}
    key = os.environ.get("STRIPE_SECRET_KEY")
    if key:
        try:
            import stripe  # type: ignore
            stripe.api_key = key
            session = stripe.checkout.Session.create(
                mode="payment",
                line_items=[{"price_data": {
                    "currency": "usd",
                    "product_data": {"name": "KEEL Team (weekly)"},
                    "unit_amount": PRICE_CENTS}, "quantity": 1}],
                success_url=f"{base_url}/app#/billing?checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
                cancel_url=f"{base_url}/app#/billing?checkout=cancel",
                metadata={"account": account, "plan": "team"})
            return {"mode": "stripe", "url": session.url, "id": session.id}
        except Exception as e:
            return {"mode": "error", "error": f"stripe error: {e}"}
    # no Stripe keys → local evaluation path (clearly labeled)
    return {"mode": "dev", "url": None,
            "message": "Stripe is not configured. For local evaluation, activate "
                       "the Team plan with the unlock code (dev only). Set "
                       "STRIPE_SECRET_KEY + STRIPE_WEBHOOK_SECRET to take real "
                       "payments.",
            "unlock_hint": "POST /api/billing/activate {\"code\": \"<KEEL_UNLOCK_CODE>\"}"}


def _claim_payment(reference: str) -> bool:
    """Record a provider payment id as spent. False if already used.

    Without this, one genuine payment's signed callback can be replayed to
    activate a licence again and again — the signature stays valid forever.
    """
    if not reference:
        return False
    store = _bill_store()
    used = list(store.kv_get(_USED_PAYMENTS) or [])
    if reference in used:
        return False
    used.append(reference)
    store.kv_set(_USED_PAYMENTS, used[-5000:])
    return True


def confirm_checkout(params: dict[str, Any],
                     session_account: str = "") -> dict[str, Any]:
    """Confirm a completed payment from the return redirect and activate.
    Routes by provider; verifies the signature; works without a webhook.

    `session_account` is the authenticated caller. The account to credit is
    ALWAYS taken from provider-authenticated data (a signed reference, or a
    session fetched from the provider) and cross-checked against it — never
    from a caller-supplied field.
    """
    import hashlib, hmac
    # ── Razorpay payment-link callback ──
    if params.get("provider") == "razorpay" or params.get("razorpay_payment_link_id"):
        secret = os.environ.get("RAZORPAY_KEY_SECRET", "")
        plink = params.get("razorpay_payment_link_id", "")
        ref = params.get("razorpay_payment_link_reference_id", "")
        pstatus = params.get("razorpay_payment_link_status", "")
        pid = params.get("razorpay_payment_id", "")
        sig = params.get("razorpay_signature", "")
        expected = hmac.new(secret.encode(),
                            f"{plink}|{ref}|{pstatus}|{pid}".encode(),
                            hashlib.sha256).hexdigest()
        if not (secret and hmac.compare_digest(expected, sig) and pstatus == "paid"):
            return {"activated": False, "error": "razorpay signature/status invalid"}
        # reference_id is inside the HMAC above, so this account is authentic.
        acct = ref.split(".", 1)[0] if ref.startswith("acct_") else ""
        if not acct:
            return {"activated": False,
                    "error": "payment is not linked to an account; contact support "
                             "with your payment id"}
        if session_account and acct != session_account:
            return {"activated": False,
                    "error": "this payment belongs to a different account"}
        if not _claim_payment(pid):
            return {"activated": False, "error": "this payment was already redeemed"}
        lic = activate(acct, "team", source="razorpay", reference=pid)
        return {"activated": True, "license": _public(lic)}
    # ── Stripe checkout session ──
    session_id = params.get("session_id", "")
    key = os.environ.get("STRIPE_SECRET_KEY")
    if not key:
        return {"activated": False, "error": "stripe not configured"}
    try:
        import stripe  # type: ignore
        stripe.api_key = key
        # Fetched from Stripe over an authenticated API call, so this metadata
        # is authentic — unlike anything in the caller's request.
        session = stripe.checkout.Session.retrieve(session_id)
        if session.get("payment_status") == "paid":
            acct = (session.get("metadata") or {}).get("account", "")
            if not acct:
                return {"activated": False,
                        "error": "payment is not linked to an account; contact "
                                 "support with your session id"}
            if session_account and acct != session_account:
                return {"activated": False,
                        "error": "this payment belongs to a different account"}
            if not _claim_payment(session_id):
                return {"activated": False, "error": "this payment was already redeemed"}
            lic = activate(acct, "team", source="stripe", reference=session_id)
            return {"activated": True, "license": _public(lic)}
        return {"activated": False, "error": "payment not completed"}
    except Exception as e:
        return {"activated": False, "error": str(e)}


def handle_webhook(payload: bytes, sig_header: str) -> dict[str, Any]:
    """Stripe webhook: activate on checkout.session.completed."""
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET")
    if not secret:
        return {"ok": False, "error": "no webhook secret configured"}
    try:
        import stripe  # type: ignore
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except Exception as e:
        return {"ok": False, "error": f"signature verification failed: {e}"}
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        if session.get("payment_status") == "paid":
            acct = (session.get("metadata") or {}).get("account", "")
            sid = session.get("id", "")
            if not acct:
                return {"ok": True, "activated": False, "error": "no account in metadata"}
            if not _claim_payment(sid):     # Stripe retries; stay idempotent
                return {"ok": True, "activated": False, "error": "already redeemed"}
            activate(acct, "team", source="stripe:webhook", reference=sid)
            return {"ok": True, "activated": True}
    return {"ok": True, "activated": False, "type": event["type"]}


def handle_razorpay_webhook(payload: bytes, sig_header: str) -> dict[str, Any]:
    """Razorpay webhook: activate on payment_link.paid / payment.captured."""
    import hashlib, hmac, json as _json
    secret = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
    if not secret:
        return {"ok": False, "error": "no razorpay webhook secret configured"}
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig_header or ""):
        return {"ok": False, "error": "signature verification failed"}
    event = _json.loads(payload)
    if event.get("event") in ("payment_link.paid", "payment.captured", "order.paid"):
        pl = event.get("payload", {})
        notes = (pl.get("payment_link", {}).get("entity", {}).get("notes")
                 or pl.get("payment", {}).get("entity", {}).get("notes") or {})
        # `notes` come from Razorpay inside the HMAC-verified body, so they are
        # authentic — unlike the callback's query string.
        acct = notes.get("account", "")
        pid = str(pl.get("payment", {}).get("entity", {}).get("id", ""))
        if not acct:
            return {"ok": True, "activated": False, "error": "no account in notes"}
        if not _claim_payment(pid):
            # Razorpay retries webhooks; this makes activation idempotent
            # rather than extending the licence on every redelivery.
            return {"ok": True, "activated": False, "error": "already redeemed"}
        activate(acct, "team", source="razorpay:webhook", reference=pid)
        return {"ok": True, "activated": True}
    return {"ok": True, "activated": False, "type": event.get("event")}


def dev_activate(account_id: str, code: str) -> dict[str, Any]:
    """Local evaluation unlock. DISABLED in production: it works only when
    auth is not required (self-host/local) OR an explicit KEEL_UNLOCK_CODE is
    configured. It never accepts a built-in default in a hardened deployment,
    so it can never bypass the paywall on a real server."""
    from . import accounts
    configured = os.environ.get("KEEL_UNLOCK_CODE")
    if accounts.auth_required() and not configured:
        return {"activated": False,
                "error": "dev activation is disabled in production; pay via the "
                         "configured provider, or set KEEL_UNLOCK_CODE to enable"}
    expected = configured or "DEV-UNLOCK"
    if not code or not hmac_compare(code, expected):
        return {"activated": False, "error": "invalid unlock code"}
    lic = activate(account_id, "team", source="dev-unlock")
    return {"activated": True, "license": _public(lic),
            "note": "dev/manual activation — for evaluation, not a paid record"}


def hmac_compare(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(a, b)


def deactivate(account_id: str = "acct_default") -> None:
    _bill_store().kv_set(_lkey(account_id), None)


def _public(lic: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in lic.items() if k != "signature"}
