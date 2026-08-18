"""Rate limiting for the endpoints that are cheap to call and expensive to lose.

Three distinct risks, so three distinct budgets rather than one global cap:

  login    — password guessing. Slow it per-account AND per-IP, because an
             attacker spraying one password across many accounts never trips a
             per-account counter.
  signup   — account flooding. An open signup with no limit is a free way to
             fill the store and burn a shared model quota.
  expensive — anything that costs money or CPU downstream (model inference,
             audit-pack assembly, replay). These are authenticated, so the
             budget is per account rather than per IP.

Deliberately in-process and dependency-free. A single-instance deployment is
what KEEL actually ships as, and an in-process limiter that works beats a Redis
one that isn't configured. If this ever runs multi-instance the limits become
per-instance, which is why `state()` reports the backend honestly rather than
implying a cluster-wide guarantee.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Iterable

# name → (max events, window seconds)
BUDGETS: dict[str, tuple[int, float]] = {
    "login": (8, 300.0),         # 8 attempts / 5 min per identity
    "login_ip": (30, 300.0),     # 30 / 5 min from one address (shared NATs exist)
    "signup": (5, 3600.0),       # 5 new accounts / hour per address
    "expensive": (60, 60.0),     # 60 / min per account
    "gateway": (600, 60.0),      # the hot path: generous, but not unbounded
}

_DISABLED = os.environ.get("KEEL_RATELIMIT", "1").lower() in ("0", "false", "off")

_hits: dict[str, deque[float]] = {}
_lock = threading.Lock()
_last_sweep = 0.0


def _sweep(now: float) -> None:
    """Drop buckets that are entirely outside their window.

    Without this, one request per unique IP grows `_hits` forever — a slow
    memory leak that is also a trivially cheap remote resource attack.
    Caller must hold `_lock`.
    """
    global _last_sweep
    # A _last_sweep in the FUTURE relative to `now` must not disable sweeping
    # forever — that would silently reintroduce the unbounded-growth leak.
    elapsed = now - _last_sweep
    if 0.0 <= elapsed < 60.0:
        return
    _last_sweep = now
    widest = max(w for _, w in BUDGETS.values())
    for key in [k for k, v in _hits.items() if not v or now - v[-1] > widest]:
        _hits.pop(key, None)


def check(budget: str, identity: str) -> tuple[bool, float]:
    """Record an attempt against `budget` for `identity`.

    Returns (allowed, retry_after_seconds). retry_after is 0 when allowed.
    Fails OPEN on an unknown budget name: a typo must not lock users out of
    their own account.
    """
    if _DISABLED or budget not in BUDGETS:
        return True, 0.0
    limit, window = BUDGETS[budget]
    key = f"{budget}|{identity}"
    now = time.monotonic()
    with _lock:
        _sweep(now)
        q = _hits.setdefault(key, deque())
        cutoff = now - window
        while q and q[0] < cutoff:
            q.popleft()
        if len(q) >= limit:
            # retry when the oldest event in the window falls out of it
            return False, max(0.0, q[0] + window - now)
        q.append(now)
        return True, 0.0


def reset(budget: str = "", identity: str = "") -> None:
    """Forget recorded attempts.

    Called on a SUCCESSFUL login so a user who mistyped twice and then got it
    right isn't still carrying a near-exhausted budget. With no arguments,
    clears everything (tests).
    """
    global _last_sweep
    with _lock:
        if not budget:
            _hits.clear()
            _last_sweep = 0.0          # so the next check re-arms the sweeper
            return
        if identity:
            _hits.pop(f"{budget}|{identity}", None)
            return
        for k in [k for k in _hits if k.startswith(budget + "|")]:
            _hits.pop(k, None)


def client_ip(headers: Iterable[tuple[bytes, bytes]] | dict, fallback: str = "") -> str:
    """The caller's address, trusting a proxy header only when told to.

    KEEL runs behind a proxy in production (Render terminates TLS), so
    X-Forwarded-For is the real client. But that header is caller-supplied and
    trivially spoofed, so honouring it when NOT behind a proxy would let an
    attacker mint a fresh rate-limit identity per request. Hence the explicit
    KEEL_TRUSTED_PROXY opt-in.
    """
    if os.environ.get("KEEL_TRUSTED_PROXY", "0") != "1":
        return fallback or "unknown"
    get = headers.get if isinstance(headers, dict) else None
    xff = (get("x-forwarded-for", "") if get else "") or ""
    # leftmost entry is the original client; the rest are proxies
    return (xff.split(",")[0].strip() or fallback or "unknown")


def state() -> dict:
    """Operational visibility, and an honest note about the guarantee."""
    with _lock:
        tracked = len(_hits)
    return {
        "enabled": not _DISABLED,
        "backend": "in-process",
        "scope": "per server instance — limits are NOT shared across replicas",
        "trusts_forwarded_for": os.environ.get("KEEL_TRUSTED_PROXY", "0") == "1",
        "budgets": {k: {"limit": n, "window_seconds": w}
                    for k, (n, w) in BUDGETS.items()},
        "tracked_identities": tracked,
    }
