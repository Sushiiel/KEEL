"""Entitlements — every feature is free for everyone.

KEEL has no paid tier and takes no payments. There is no payment provider, no
checkout, no licence to buy, and nothing to expire. This module exists only so
the rest of the codebase has one honest place to ask "what may this account
do?", and the answer is always "everything".

It is kept rather than deleted for two reasons: `require_feature` call sites
stay readable, and if a paid tier is ever introduced the gate has one obvious
home instead of being scattered across handlers. Until then every function here
is deliberately unconditional — no hidden switch, no env var that quietly
locks a feature.
"""
from __future__ import annotations

from typing import Any

PLAN = "free"

# Everything KEEL can do. Named so a feature check reads clearly at its call
# site and so the console can enumerate what an account has.
ALL_FEATURES: frozenset[str] = frozenset({
    "managed_hosting", "hsm_keys", "approval_integrations",
    "evidence_export_full", "evidence_scheduling", "priority_support",
    "sso", "rbac", "private_deploy", "worm_retention", "custom_policy",
})

# Retained so `FEATURES[plan]` keeps working for any caller that still asks by
# plan name. Every plan maps to the same complete set.
FEATURES: dict[str, frozenset[str]] = {
    "free": ALL_FEATURES, "team": ALL_FEATURES, "enterprise": ALL_FEATURES,
}


def provider() -> str:
    """No payment rail is configured, by design."""
    return "free"


def price() -> dict[str, Any]:
    return {"amount": 0, "currency": "USD", "display": "Free",
            "period": "forever"}


def entitlement(account_id: str = "acct_default") -> dict[str, Any]:
    """What this account may do. Unconditional: the full feature set, forever.

    `account_id` is accepted so call sites don't have to change if a tier is
    ever added, but it does not affect the answer today.
    """
    return {"plan": PLAN, "features": sorted(ALL_FEATURES), "valid": True,
            "seats": None,               # unlimited; None reads as "no limit"
            "expires_at": None,          # never
            "source": "free", "amount": 0, "currency": "USD"}


def has_feature(account_id: str = "acct_default", feature: str = "") -> bool:
    """True for every feature KEEL ships. Unknown names are still False, so a
    typo in a require_feature() call fails loudly instead of silently passing.
    """
    return feature in ALL_FEATURES


def status(account_id: str = "acct_default") -> dict[str, Any]:
    """What the console's billing view renders."""
    ent = entitlement(account_id)
    pr = price()
    return {**ent, "provider": "free", "price": pr,
            "price_display": pr["display"], "price_usd": 0,
            "payments_live": False,
            "all_features": sorted(ALL_FEATURES),
            "all_team_features": sorted(ALL_FEATURES),   # legacy key
            "locked_features": []}
