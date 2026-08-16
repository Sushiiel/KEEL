"""User accounts, authentication, sessions, and per-account API keys.

Passwords are scrypt-hashed (stdlib, memory-hard). Sessions are stateless
signed tokens (HMAC-SHA256 over a server secret that persists in the data
dir). Each account gets an API key so its agents can authenticate to the
gateway. Entitlements (the paid plan) are keyed per account.

Auth is enforced in production (KEEL_AUTH_REQUIRED=1). For local self-host it
falls back to a single 'default' account so nothing needs configuring to try.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from typing import Any, Optional

from .config import DATA_DIR
from .store import Store, get_store

SESSION_TTL = 30 * 86400
_ACCOUNTS = "accounts"                 # kv: email -> account record
_BY_KEY = "accounts_by_key"            # kv: api_key -> email
_SECRET_FILE = DATA_DIR / "session_secret"


def auth_required() -> bool:
    return os.environ.get("KEEL_AUTH_REQUIRED", "0") == "1"


def _store() -> Store:
    return get_store("accounts")


def _secret() -> bytes:
    env = os.environ.get("KEEL_SECRET_KEY")
    if env:
        return env.encode()
    if _SECRET_FILE.exists():
        return _SECRET_FILE.read_bytes()
    sec = secrets.token_bytes(32)
    _SECRET_FILE.write_bytes(sec)
    try:
        os.chmod(_SECRET_FILE, 0o600)
    except OSError:
        pass
    return sec


# ── password hashing ─────────────────────────────────────────────────────────

def _hash_pw(password: str, salt: Optional[bytes] = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return salt.hex(), dk.hex()


def _verify_pw(password: str, salt_hex: str, hash_hex: str) -> bool:
    _, dk = _hash_pw(password, bytes.fromhex(salt_hex))
    return hmac.compare_digest(dk, hash_hex)


# ── accounts ─────────────────────────────────────────────────────────────────

def create_account(email: str, password: str, name: str = "") -> dict[str, Any]:
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("a valid email is required")
    if len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    accounts = _store().kv_get(_ACCOUNTS, {})
    if email in accounts:
        raise ValueError("an account with this email already exists")
    salt, pw = _hash_pw(password)
    api_key = "keel_ak_" + secrets.token_urlsafe(24)
    acct = {"account_id": "acct_" + uuid.uuid4().hex[:16], "email": email,
            "name": name or email.split("@")[0], "pw_salt": salt, "pw_hash": pw,
            "api_key": api_key, "created_at": time.time(), "role": "owner"}
    accounts[email] = acct
    _store().kv_set(_ACCOUNTS, accounts)
    by_key = _store().kv_get(_BY_KEY, {})
    by_key[api_key] = email
    _store().kv_set(_BY_KEY, by_key)
    return _public(acct)


def authenticate(email: str, password: str) -> Optional[dict[str, Any]]:
    acct = _store().kv_get(_ACCOUNTS, {}).get(email.strip().lower())
    if acct and _verify_pw(password, acct["pw_salt"], acct["pw_hash"]):
        return acct
    return None


def _default_account() -> dict[str, Any]:
    """Single-tenant fallback for local self-host (auth not required)."""
    accounts = _store().kv_get(_ACCOUNTS, {})
    if "default@local" not in accounts:
        api_key = "keel_ak_" + secrets.token_urlsafe(24)
        acct = {"account_id": "acct_default", "email": "default@local",
                "name": "Local", "pw_salt": "", "pw_hash": "",
                "api_key": api_key, "created_at": time.time(), "role": "owner"}
        accounts["default@local"] = acct
        _store().kv_set(_ACCOUNTS, accounts)
        by_key = _store().kv_get(_BY_KEY, {})
        by_key[api_key] = "default@local"
        _store().kv_set(_BY_KEY, by_key)
        return acct
    return accounts["default@local"]


def account_by_email(email: str) -> Optional[dict[str, Any]]:
    return _store().kv_get(_ACCOUNTS, {}).get((email or "").strip().lower())


def account_by_api_key(api_key: str) -> Optional[dict[str, Any]]:
    email = _store().kv_get(_BY_KEY, {}).get(api_key or "")
    return _store().kv_get(_ACCOUNTS, {}).get(email) if email else None


def rotate_api_key(email: str) -> Optional[str]:
    accounts = _store().kv_get(_ACCOUNTS, {})
    acct = accounts.get(email)
    if not acct:
        return None
    by_key = _store().kv_get(_BY_KEY, {})
    by_key.pop(acct.get("api_key", ""), None)
    new_key = "keel_ak_" + secrets.token_urlsafe(24)
    acct["api_key"] = new_key
    accounts[email] = acct
    by_key[new_key] = email
    _store().kv_set(_ACCOUNTS, accounts)
    _store().kv_set(_BY_KEY, by_key)
    return new_key


# ── sessions (stateless, signed) ─────────────────────────────────────────────

def issue_session(account: dict[str, Any]) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(
        {"email": account["email"], "exp": time.time() + SESSION_TTL}).encode()).decode()
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def account_from_session(token: str) -> Optional[dict[str, Any]]:
    if not token or "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    expected = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
    except Exception:
        return None
    if data.get("exp", 0) < time.time():
        return None
    return account_by_email(data.get("email", ""))


def resolve(session_token: str = "", api_key: str = "") -> Optional[dict[str, Any]]:
    """Return the account for a request, honoring the auth mode."""
    if session_token:
        acct = account_from_session(session_token)
        if acct:
            return acct
    if api_key:
        acct = account_by_api_key(api_key)
        if acct:
            return acct
    if not auth_required():
        return _default_account()
    return None


def _public(acct: dict[str, Any]) -> dict[str, Any]:
    return {"account_id": acct["account_id"], "email": acct["email"],
            "name": acct.get("name", ""), "api_key": acct.get("api_key", ""),
            "role": acct.get("role", "owner"), "created_at": acct.get("created_at")}


def public(acct: dict[str, Any]) -> dict[str, Any]:
    return _public(acct)
