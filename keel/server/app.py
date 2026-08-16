"""KEEL API server: REST + SSE + A2A + the operator UI.

Multi-domain: every endpoint takes a `domain` query parameter (default
telecom). Each domain is an isolated workspace — its own store, causal graph,
calibration corpus, transparency log — seeded lazily on first access. The UI
is the product surface; external agents use the same pipeline over A2A.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
from typing import Any, Optional

import numpy as np
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse, Response, StreamingResponse)
from fastapi.staticfiles import StaticFiles

from ..calibrate.conformal import corpus, empirical_coverage
from ..calibrate.drift import check_drift
from ..cert import authority, translog
from ..config import (AUTONOMY_TIERS, CMDP_LIMITS, CHANGE_WINDOWS,
                      CONFORMAL_ALPHA, DATA_DIR, SCHEMA_DIR, SITE_DIR, UI_DIR)
from ..domains import DEFAULT_DOMAIN, all_packs, get_pack
from ..evalx.harness import cached_report, run_replay
from ..gate.policy import OVERRIDES_KEY, tenant_autonomy
from ..hypothesis.evidence import dedupe_instances
from ..interop.a2a import agent_card, handle_rpc
from ..domains import registry as ws_registry
from ..pipeline import (Runtime, boot, execute_certificate, learn_workspace,
                        resolve_incident, run_verification, watch_tick)
from ..substrate import ingest as ing
from ..gateway import engine as gw
from .. import billing
from .. import accounts
from ..gateway.models import (ActionOutcome, ActionRequest, AgentProfile)
from ..structure.ensemble import PIN_KEY, current_graph, publish_graph
from ..structure.discovery import CausalDiscovery, AdjacencyIndex
from ..substrate.simulator import simulate_incident

# The interactive API explorer is on for self-host/dev, but off in a hardened
# deployment (auth required) unless explicitly enabled — so the endpoint
# surface isn't enumerable by anonymous visitors.
_EXPLORER = (os.environ.get("KEEL_API_EXPLORER", "").lower() in ("1", "true")
             or os.environ.get("KEEL_AUTH_REQUIRED", "0") != "1")

app = FastAPI(title="KEEL", version="0.3.0",
              description="Runtime trust layer for agentic AI",
              docs_url="/api-explorer" if _EXPLORER else None,
              redoc_url="/api-redoc" if _EXPLORER else None,
              openapi_url="/openapi.json" if _EXPLORER else None)


# ── the authentication gate ──────────────────────────────────────────────────
# Auth must be DENY-BY-DEFAULT. Relying on each handler to remember to call
# current_account() is how endpoints silently ship unauthenticated. Everything
# under /api and /a2a requires a session or an account API key; the routes
# below are the complete, explicit public surface.
_PUBLIC_EXACT = frozenset({
    "/", "/docs", "/app",                      # pages
    "/healthz", "/favicon.ico", "/robots.txt", "/sitemap.xml",
    "/api/auth/config", "/api/auth/signup",    # you must be able to sign in
    "/api/auth/login", "/api/auth/logout",
    "/api/schema/certificate",                 # the open certificate standard
    "/.well-known/agent-card.json",            # A2A discovery (signed, public)
    "/api/billing/webhook",                    # provider-signature verified
    "/api/billing/webhook/razorpay",           # provider-signature verified
})
_PUBLIC_PREFIXES = ("/site/", "/ui/", "/static/", "/.well-known/acme-challenge")
_GUARDED_PREFIXES = ("/api/", "/a2a")


@app.middleware("http")
async def authentication_gate(request: Request, call_next):
    """Deny-by-default authentication for the whole API surface."""
    if not accounts.auth_required():
        return await call_next(request)            # self-host / local mode
    path = request.url.path
    normalized = path.rstrip("/") or "/"
    if (normalized in _PUBLIC_EXACT
            or any(path.startswith(p) for p in _PUBLIC_PREFIXES)):
        return await call_next(request)
    if any(normalized == p.rstrip("/") or path.startswith(p)
           for p in _GUARDED_PREFIXES):
        auth = request.headers.get("authorization", "")
        api_key = auth[7:] if auth.lower().startswith("bearer ") else ""
        if accounts.resolve(session_token=request.cookies.get("keel_session", ""),
                            api_key=api_key) is None:
            return JSONResponse(
                {"error": "authentication required",
                 "hint": "sign in at /app, or send Authorization: Bearer <API key>"},
                status_code=401)
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Baseline browser hardening. A product that sells trust must not ship a
    console that can be clickjacked or MIME-sniffed."""
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy",
                            "geolocation=(), microphone=(), camera=(), payment=()")
    resp.headers.setdefault("Content-Security-Policy", "; ".join([
        "default-src 'self'",
        # inline styles/scripts are used by the console and the Firebase snippet
        "script-src 'self' 'unsafe-inline' https://www.gstatic.com",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "font-src 'self' data:",
        "connect-src 'self' https://*.googleapis.com https://*.google-analytics.com "
        "https://*.firebaseio.com https://firebaseinstallations.googleapis.com",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ]))
    if os.environ.get("KEEL_HTTPS", "0") == "1":
        resp.headers.setdefault("Strict-Transport-Security",
                                "max-age=31536000; includeSubDomains")
    return resp

_runtimes: dict[str, Runtime] = {}
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def rt(domain: str = DEFAULT_DOMAIN) -> Runtime:
    try:
        get_pack(domain)
    except KeyError:
        raise HTTPException(404, f"unknown domain '{domain}'")
    with _locks_guard:
        lock = _locks.setdefault(domain, threading.Lock())
    with lock:
        if domain not in _runtimes:
            _runtimes[domain] = boot(domain)
        return _runtimes[domain]


@app.on_event("startup")
def _startup() -> None:
    # nothing is seeded at startup — the product starts empty; sandbox demo
    # worlds only exist when KEEL_SANDBOX=1 and are seeded on first access

    def _watch_loop():
        while True:
            time.sleep(60)
            for key in list(ws_registry.list_workspaces()):
                try:
                    r = _runtimes.get(key)
                    if r is not None and r.pack.auto_verify:
                        watch_tick(r)
                except Exception:
                    continue

    threading.Thread(target=_watch_loop, daemon=True).start()


DomainQ = Query(default=DEFAULT_DOMAIN, description="domain workspace key")

_INTEGRATIONS = "billing_integrations"
_SCHEDULES = "billing_schedules"


def current_account(request: Request, required: bool = False) -> dict[str, Any]:
    token = request.cookies.get("keel_session", "")
    api_key = ""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        api_key = auth[7:]
    acct = accounts.resolve(session_token=token, api_key=api_key)
    if acct is None and (required or accounts.auth_required()):
        raise HTTPException(401, "authentication required")
    return acct or {"account_id": "acct_default", "email": "default@local"}


def require_feature(request: Request, feature: str) -> dict[str, Any]:
    """Raise 401 if unauthenticated (when required) or 402 if the account is
    not entitled to this Team feature."""
    acct = current_account(request, required=accounts.auth_required())
    if not billing.has_feature(acct["account_id"], feature):
        raise HTTPException(402, {
            "error": "This is a Team feature. Upgrade to unlock.",
            "feature": feature, "upgrade": "/app#/billing"})
    return acct


# ── authentication ───────────────────────────────────────────────────────────

def _set_session(resp: Response, account: dict[str, Any]) -> None:
    resp.set_cookie("keel_session", accounts.issue_session(account),
                    max_age=accounts.SESSION_TTL, httponly=True, samesite="lax",
                    secure=os.environ.get("KEEL_HTTPS", "0") == "1")


@app.get("/api/auth/config")
def auth_config() -> dict[str, Any]:
    n = len(accounts._store().kv_get("accounts", {}))
    return {"auth_required": accounts.auth_required(),
            "signup_mode": os.environ.get("KEEL_SIGNUP", "open").lower(),
            "has_accounts": any(e != "default@local"
                                for e in accounts._store().kv_get("accounts", {}))}


@app.post("/api/auth/signup")
def auth_signup(body: dict[str, Any] = Body(...)) -> JSONResponse:
    # KEEL_SIGNUP: open (default) · invite (needs KEEL_INVITE_CODE) · closed
    mode = os.environ.get("KEEL_SIGNUP", "open").lower()
    if mode == "closed":
        raise HTTPException(403, "sign-ups are closed on this deployment")
    if mode == "invite":
        import hmac as _hmac
        expected = os.environ.get("KEEL_INVITE_CODE", "")
        if not expected or not _hmac.compare_digest(
                str(body.get("invite_code", "")), expected):
            raise HTTPException(403, "a valid invite code is required")
    try:
        acct = accounts.create_account(body.get("email", ""),
                                       body.get("password", ""),
                                       body.get("name", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    full = accounts.account_by_email(acct["email"])
    resp = JSONResponse(acct)
    _set_session(resp, full)
    return resp


@app.post("/api/auth/login")
def auth_login(body: dict[str, Any] = Body(...)) -> JSONResponse:
    acct = accounts.authenticate(body.get("email", ""), body.get("password", ""))
    if acct is None:
        raise HTTPException(401, "invalid email or password")
    resp = JSONResponse(accounts.public(acct))
    _set_session(resp, acct)
    return resp


@app.post("/api/auth/logout")
def auth_logout() -> JSONResponse:
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("keel_session")
    return resp


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict[str, Any]:
    acct = current_account(request, required=accounts.auth_required())
    full = accounts.account_by_email(acct["email"]) or acct
    return {**accounts.public(full),
            "entitlement": billing.entitlement(acct["account_id"])}


@app.post("/api/auth/rotate-key")
def auth_rotate(request: Request) -> dict[str, Any]:
    acct = current_account(request, required=True)
    key = accounts.rotate_api_key(acct["email"])
    return {"api_key": key}


# ── billing & entitlements ───────────────────────────────────────────────────

@app.get("/api/billing/status")
def billing_status(request: Request) -> dict[str, Any]:
    return billing.status(current_account(request)["account_id"])


@app.post("/api/billing/checkout")
def billing_checkout(request: Request,
                     body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    base = str(request.base_url).rstrip("/")
    return billing.create_checkout(base, account=current_account(request)["account_id"])


@app.post("/api/billing/confirm")
def billing_confirm(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return billing.confirm_checkout(body)


@app.post("/api/billing/webhook/razorpay")
async def billing_webhook_razorpay(request: Request) -> dict[str, Any]:
    payload = await request.body()
    sig = request.headers.get("x-razorpay-signature", "")
    return billing.handle_razorpay_webhook(payload, sig)


@app.post("/api/billing/activate")
def billing_activate(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    res = billing.dev_activate(current_account(request)["account_id"],
                               body.get("code", ""))
    if not res.get("activated"):
        raise HTTPException(403, res.get("error", "activation failed"))
    return res


@app.post("/api/billing/webhook")
async def billing_webhook(request: Request) -> dict[str, Any]:
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    return billing.handle_webhook(payload, sig)


@app.post("/api/billing/deactivate")
def billing_deactivate(request: Request) -> dict[str, Any]:
    billing.deactivate(current_account(request)["account_id"])
    return {"deactivated": True, "plan": "free"}


# ── Team feature: Slack / ticketing approval-queue integration ───────────────

@app.get("/api/integrations")
def integrations_get(request: Request) -> dict[str, Any]:
    acct = current_account(request)
    cfg = gw.gw_store().kv_get(_INTEGRATIONS, {})
    return {"configured": {k: bool(v) for k, v in cfg.items()},
            "entitled": billing.has_feature(acct["account_id"], "approval_integrations")}


_ALLOWED_HOOK_HOSTS = {"hooks.slack.com", "discord.com", "discordapp.com"}


@app.put("/api/integrations/slack")
def integrations_slack(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    require_feature(request, "approval_integrations")
    url = str(body.get("webhook_url", "")).strip()
    if url:
        from urllib.parse import urlparse
        u = urlparse(url)
        if u.scheme != "https" or u.hostname not in _ALLOWED_HOOK_HOSTS:
            raise HTTPException(400, "webhook must be an https URL on an allowed "
                                f"host {sorted(_ALLOWED_HOOK_HOSTS)} (SSRF guard)")
    cfg = gw.gw_store().kv_get(_INTEGRATIONS, {})
    cfg["slack_webhook"] = url
    gw.gw_store().kv_set(_INTEGRATIONS, cfg)
    return {"ok": True, "slack": bool(url)}


def _notify_escalation(decision: dict[str, Any]) -> None:
    """Fire the configured Slack/ticketing hook when an action escalates —
    Team feature, silently skipped when unentitled or unconfigured."""
    owner = gw.agent_owner(decision.get("agent_id", "")) or "acct_default"
    if decision.get("decision") != "ESCALATE" or not billing.has_feature(owner, "approval_integrations"):
        return
    cfg = gw.gw_store().kv_get(_INTEGRATIONS, {})
    hook = cfg.get("slack_webhook")
    if not hook:
        return
    try:
        import json as _json, urllib.request
        text = (f":lock: KEEL approval needed — *{decision['agent_id']}* wants "
                f"*{decision['action_class']}*. {(decision.get('reasons') or [''])[0][:160]}")
        urllib.request.urlopen(urllib.request.Request(
            hook, data=_json.dumps({"text": text}).encode(),
            headers={"Content-Type": "application/json"}), timeout=5)
    except Exception:
        pass


# ── Team feature: evidence-pack scheduling ───────────────────────────────────

@app.post("/api/gateway/schedule-evidence")
def schedule_evidence(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    require_feature(request, "evidence_scheduling")
    sched = gw.gw_store().kv_get(_SCHEDULES, {})
    sched["evidence"] = {"every_hours": float(body.get("every_hours", 24)),
                         "sample": int(body.get("sample", 25)),
                         "next_at": time.time() + float(body.get("every_hours", 24)) * 3600}
    gw.gw_store().kv_set(_SCHEDULES, sched)
    return {"scheduled": sched["evidence"]}


# ── Team feature: hardened key mode ──────────────────────────────────────────

@app.put("/api/security/key-mode")
def key_mode(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    require_feature(request, "hsm_keys")
    mode = body.get("mode", "managed")
    gw.gw_store().kv_set("security_key_mode", mode)
    return {"key_mode": mode, "note": "HSM/KMS-backed signing (Team)"}


# ── domains ──────────────────────────────────────────────────────────────────

@app.get("/api/integrations/status")
def integrations_status() -> dict[str, Any]:
    from ..integrations import status as _st
    return _st()


_FAVICON = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
    "<rect width='32' height='32' rx='6' fill='%23FFFFFF' stroke='%23DDE2D9'/>"
    "<path d='M8 6v20M8 16l12-10M8 16l12 10' stroke='%232F49C9' stroke-width='2.6' "
    "fill='none' stroke-linecap='round'/></svg>")

# paths people actually type or that other sites link to → send them somewhere real
_FRIENDLY_REDIRECTS = {
    "/index.html": "/", "/home": "/", "/start": "/",
    "/pricing": "/#pricing", "/price": "/#pricing", "/plans": "/#pricing",
    "/features": "/#features", "/use-cases": "/#use-cases",
    "/login": "/app", "/signin": "/app", "/sign-in": "/app",
    "/signup": "/app", "/sign-up": "/app", "/register": "/app",
    "/console": "/app", "/dashboard": "/app", "/gateway": "/app#/gateway",
    "/billing": "/app#/billing", "/upgrade": "/app#/billing",
    "/documentation": "/docs", "/doc": "/docs", "/help": "/docs",
    "/api": "/docs#api", "/sdk": "/docs#sdk", "/quickstart": "/docs#quickstart",
    "/github": "https://github.com/Sushiiel/KEEL",
}


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(_FAVICON, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.exception_handler(404)
async def not_found(request: Request, exc) -> Response:
    """Never show a bare JSON 'Not Found' to a person. APIs get JSON; browsers
    get a redirect to the right page, or a real 404 page with a way out."""
    path = request.url.path.rstrip("/") or "/"
    target = _FRIENDLY_REDIRECTS.get(path.lower())
    if target:
        return RedirectResponse(target, status_code=307)
    # never intercept ACME HTTP-01 challenges — a proxy in front of us may
    # rely on this path for TLS issuance/renewal. Plain 404, no JSON body.
    if path.startswith("/.well-known/acme-challenge"):
        return Response(status_code=404)
    if path.startswith(("/api", "/a2a", "/.well-known")):
        return JSONResponse({"error": "not found", "path": path,
                             "docs": "https://keel.best/docs"}, status_code=404)
    return HTMLResponse(_404_PAGE, status_code=404)


_404_PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Page not found — KEEL</title><link rel="stylesheet" href="/site/site.css">
</head><body>
<header class="nav"><div class="wrap">
  <a href="/" class="brand" style="text-decoration:none">KE<b>E</b>L</a>
  <nav><a href="/">Home</a><a href="/docs">Docs</a><a href="/app">Console</a></nav>
  <span class="spacer"></span><a href="/app" class="btn primary">Open console &rarr;</a>
</div></header>
<section class="hero"><div class="wrap">
  <span class="eyebrow">404</span>
  <h1>That page doesn't exist.</h1>
  <p class="lede">The link may be out of date. Everything KEEL does lives in one of
  these three places.</p>
  <div class="cta">
    <a href="/" class="btn primary">Product overview</a>
    <a href="/docs" class="btn">Documentation</a>
    <a href="/app" class="btn">Operator console</a>
  </div>
</div></section></body></html>"""


@app.get("/robots.txt")
def robots() -> Response:
    return Response("User-agent: *\nAllow: /\nDisallow: /app\nDisallow: /api\n"
                    "Sitemap: https://keel.best/sitemap.xml\n", media_type="text/plain")


@app.get("/sitemap.xml")
def sitemap() -> Response:
    urls = "".join(f"<url><loc>https://keel.best{p}</loc></url>" for p in ("/", "/docs"))
    return Response(f'<?xml version="1.0" encoding="UTF-8"?>'
                    f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>',
                    media_type="application/xml")


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {"status": "ok", "service": "keel", "version": "0.3.0"}


@app.get("/api/domains")
def domains() -> list[dict[str, Any]]:
    out = []
    for key, pack in sorted(all_packs().items()):
        seeded = key in _runtimes or (DATA_DIR / f"keel-{key}.db").exists()
        out.append({"key": key, "name": pack.name, "tenant": pack.tenant,
                    "icon": pack.icon, "world_title": pack.world_title,
                    "seeded": seeded, "sandbox": True})
    for key, ws in sorted(ws_registry.list_workspaces().items()):
        out.append({"key": key, "name": ws["name"], "tenant": ws["tenant"],
                    "icon": "◆", "world_title": f"{ws['name']} — connected data",
                    "seeded": True, "sandbox": False})
    return out


# ── workspaces (bring your own data) ─────────────────────────────────────────

@app.post("/api/workspaces")
def create_workspace(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    name = str(body.get("name", "")).strip()
    if not name:
        raise HTTPException(400, "workspace name required")
    ws = ws_registry.create_workspace(name, tenant=body.get("tenant", ""),
                                      **(body.get("profile") or {}))
    return ws


@app.get("/api/workspaces/{key}")
def get_workspace(key: str) -> dict[str, Any]:
    ws = ws_registry.get_workspace(key)
    if ws is None:
        raise HTTPException(404, "unknown workspace")
    r = rt(key)
    status = {
        "entities": len(r.store.entities()),
        "topology_edges": len(r.store.topology_at(time.time())),
        "events": sum(1 for _ in r.store.events_between(0, time.time() + 1)),
        "incidents": len(r.store.incidents(limit=1000)),
        "labeled": sum(1 for i in r.store.incidents(limit=1000)
                       if i.status == "resolved" and i.ground_truth),
        "graph_edges": len(r.type_edges),
        "calibration_n": len(corpus(r.store)),
    }
    return {**ws, "status": status}


@app.put("/api/workspaces/{key}/profile")
def update_workspace(key: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    ws = ws_registry.update_profile(key, body)
    if ws is None:
        raise HTTPException(404, "unknown workspace")
    _runtimes.pop(key, None)          # rebuild runtime with the new profile
    return ws


@app.get("/api/workspaces/{key}/types")
def workspace_types(key: str) -> dict[str, Any]:
    return ing.suggest_vocabulary(rt(key).store)


# ── ingestion ────────────────────────────────────────────────────────────────

@app.post("/api/ingest/events")
def ingest_events_ep(body: Any = Body(...), domain: str = DomainQ) -> dict[str, Any]:
    rows = body if isinstance(body, list) else body.get("events", [])
    res = ing.ingest_events(rt(domain).store, rows)
    res["watch"] = watch_tick(rt(domain))
    return res


@app.post("/api/ingest/topology")
def ingest_topology_ep(body: dict[str, Any] = Body(...),
                       domain: str = DomainQ) -> dict[str, Any]:
    return ing.ingest_topology(rt(domain).store, body)


@app.post("/api/ingest/incidents")
def ingest_incidents_ep(body: Any = Body(...), domain: str = DomainQ) -> dict[str, Any]:
    rows = body if isinstance(body, list) else body.get("incidents", [])
    return ing.ingest_labeled_incidents(rt(domain).store, rows)


@app.post("/api/webhook/alertmanager")
def alertmanager_webhook(body: dict[str, Any] = Body(...),
                         domain: str = DomainQ) -> dict[str, Any]:
    res = ing.ingest_alertmanager(rt(domain).store, body)
    res["watch"] = watch_tick(rt(domain))
    return res


@app.post("/api/learn")
def learn_ep(domain: str = DomainQ) -> dict[str, Any]:
    return learn_workspace(rt(domain))


@app.post("/api/incidents/{incident_id}/resolve")
def resolve_ep(incident_id: str, body: dict[str, Any] = Body(...),
               domain: str = DomainQ) -> dict[str, Any]:
    res = resolve_incident(rt(domain), incident_id,
                           str(body.get("root_cause", "")),
                           verified_by=body.get("by", "operator"))
    if "error" in res:
        raise HTTPException(400, res["error"])
    return res


# ── overview ─────────────────────────────────────────────────────────────────

@app.get("/api/overview")
def overview(domain: str = DomainQ) -> dict[str, Any]:
    r = rt(domain)
    store = r.store
    incidents = store.incidents(limit=250)
    certs = store.certificates(limit=250)
    drift = check_drift(store)
    auto = tenant_autonomy(store)
    verdicts: dict[str, int] = {}
    for c in certs:
        verdicts[c.verdict] = verdicts.get(c.verdict, 0) + 1
    return {
        "domain": domain, "tenant": r.pack.tenant, "domain_name": r.pack.name,
        "graph_version": r.graph_version, "edges": len(r.type_edges),
        "incidents_total": len(incidents),
        "incidents_open": sum(1 for i in incidents if i.status in ("open", "verifying")),
        "certificates": len(certs), "verdicts": verdicts,
        "calibration_n": len(corpus(store)),
        "coverage": empirical_coverage(store),
        "drift": drift.model_dump(), "autonomy": auto,
        "resolver": store.kv_get("resolver_metrics", {}),
        "translog_size": len(store.translog()),
        "translog_root": translog.current_root(store),
        "alpha": CONFORMAL_ALPHA,
    }


@app.get("/api/network")
def network(domain: str = DomainQ) -> dict[str, Any]:
    r = rt(domain)
    store = r.store
    failed: set[str] = set()
    for i in store.incidents(limit=50):
        if i.status in ("open", "verifying", "certified"):
            failed |= set(i.entities)
    return {
        "world_title": r.pack.world_title,
        "entities": [e.model_dump() for e in store.entities()],
        "topology": [t.model_dump() for t in store.topology_at(time.time())],
        "impacted": sorted(failed),
    }


# ── incidents ────────────────────────────────────────────────────────────────

@app.get("/api/incidents")
def incidents(domain: str = DomainQ) -> list[dict[str, Any]]:
    out = []
    for i in rt(domain).store.incidents(limit=120):
        d = i.model_dump()
        if i.status not in ("resolved",):
            d["ground_truth"] = None          # never leak the answer key
        out.append(d)
    return out


@app.get("/api/incidents/{incident_id}")
def incident_detail(incident_id: str, domain: str = DomainQ) -> dict[str, Any]:
    r = rt(domain)
    inc = r.store.incident(incident_id)
    if inc is None:
        raise HTTPException(404, "unknown incident")
    events = r.store.events_for(incident_id)
    annotated = r.hawkes.annotate(
        events, adjacency=AdjacencyIndex(r.store.topology_at(inc.t0)))
    d = inc.model_dump()
    if inc.status != "resolved":
        d["ground_truth"] = None
    certs = r.store.certificates_for_incident(incident_id)
    outage = next(iter(r.pack.outage_types), "svc.sla_breach")
    return {
        "incident": d,
        "instances": dedupe_instances(annotated),
        "alarms": [e.model_dump() for e in annotated[:400]],
        "certificates": [c.model_dump() for c in certs],
        "intensity": r.hawkes.intensity_trace(annotated, outage, n=80),
    }


@app.post("/api/incidents/simulate")
def simulate(body: dict[str, Any] = Body(default={}),
             domain: str = DomainQ) -> dict[str, Any]:
    r = rt(domain)
    if not r.pack.synthetic:
        raise HTTPException(400, "this workspace runs on connected data only — "
                                 "no simulation; ingest events instead")
    scenario = body.get("scenario") or next(iter(r.pack.scenarios))
    if scenario not in r.pack.scenarios:
        raise HTTPException(400, f"unknown scenario; choose from {list(r.pack.scenarios)}")
    rng = np.random.default_rng()
    inc_id = f"INC-L{int(time.time()) % 1_000_000:06d}"
    inc, events = simulate_incident(r.store, r.pack, scenario,
                                    time.time() - 600, rng, inc_id, status="open")
    r.store.put_incident(inc)
    r.store.add_events(events)
    d = inc.model_dump()
    d["ground_truth"] = None
    return {"incident": d}


@app.get("/api/scenarios")
def scenarios(domain: str = DomainQ) -> list[dict[str, str]]:
    r = rt(domain)
    return [{"key": k, "severity": s.severity}
            for k, s in r.pack.scenarios.items()]


# ── verification (SSE) ───────────────────────────────────────────────────────

@app.get("/api/incidents/{incident_id}/verify")
def verify_stream(incident_id: str, claim: Optional[str] = None,
                  claimant: str = "keel-hypothesizer",
                  domain: str = DomainQ) -> StreamingResponse:
    r = rt(domain)

    def gen():
        q: "queue.Queue[Optional[dict]]" = queue.Queue()

        def worker():
            try:
                inc = r.store.incident(incident_id)
                if inc and inc.status == "open":
                    inc.status = "verifying"
                    r.store.put_incident(inc)
                for step in run_verification(r, incident_id, claim_variable=claim,
                                             claimant=claimant):
                    q.put(step)
            except Exception as e:
                q.put({"stage": "error", "status": "failed", "detail": str(e),
                       "data": {}})
            finally:
                q.put(None)

        threading.Thread(target=worker, daemon=True).start()
        while True:
            step = q.get()
            if step is None:
                break
            yield f"data: {json.dumps(step)}\n\n"
            time.sleep(0.35)          # progressive certainty, visible to humans

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ── certificates ─────────────────────────────────────────────────────────────

@app.get("/api/certificates")
def certificates(domain: str = DomainQ) -> list[dict[str, Any]]:
    return [c.model_dump() for c in rt(domain).store.certificates(limit=120)]


def _store_for(domain: str):
    return gw.gw_store() if domain == "gateway" else rt(domain).store


@app.get("/api/certificates/{cert_id}")
def certificate(cert_id: str, domain: str = DomainQ) -> dict[str, Any]:
    store = _store_for(domain)
    cert = store.certificate(cert_id)
    if cert is None:
        raise HTTPException(404, "unknown certificate")
    proof = (translog.inclusion_proof(store, cert.log_index)
             if cert.log_index is not None else None)
    outcome = next((o.model_dump() for o in store.outcomes()
                    if o.cert_id == cert_id), None)
    return {"certificate": cert.model_dump(),
            "verification": authority.verify(cert),
            "inclusion_proof": proof, "outcome": outcome}


@app.post("/api/certificates/{cert_id}/execute")
def execute(cert_id: str, body: dict[str, Any] = Body(default={}),
            domain: str = DomainQ) -> dict[str, Any]:
    res = execute_certificate(rt(domain), cert_id,
                              approver=body.get("approver", "operator"),
                              force=bool(body.get("force", False)))
    if "error" in res:
        raise HTTPException(409, res["error"])
    return res


# ── causal graph ─────────────────────────────────────────────────────────────

@app.get("/api/graph")
def graph(domain: str = DomainQ) -> dict[str, Any]:
    r = rt(domain)
    version, rows = current_graph(r.store)
    return {"version": version, "edges": rows,
            "history": r.store.kv_get("graph_history", []),
            "pins": r.store.kv_get(PIN_KEY, [])}


@app.post("/api/graph/pin")
def pin_edge(body: dict[str, Any] = Body(...),
             domain: str = DomainQ) -> dict[str, Any]:
    r = rt(domain)
    pins = r.store.kv_get(PIN_KEY, [])
    entry = {"src": body["src"], "dst": body["dst"],
             "action": body.get("action", "pin"),
             "by": body.get("by", "operator"), "reason": body.get("reason", "")}
    pins = [p for p in pins if not (p["src"] == entry["src"]
                                    and p["dst"] == entry["dst"])]
    if entry["action"] != "clear":
        pins.append(entry)
    r.store.kv_set(PIN_KEY, pins)
    resolved = [i for i in r.store.incidents(limit=400) if i.status == "resolved"]
    seqs = [r.store.events_for(i.incident_id) for i in resolved]
    disco = CausalDiscovery(AdjacencyIndex(r.store.topology_at(time.time())),
                            sink_types=r.pack.impact_types,
                            exogenous_types=r.pack.change_types)
    edges = disco.discover([s for s in seqs if s])
    version = publish_graph(r.store, edges)
    r.refresh_graph()
    return {"version": version, "pins": pins}


# ── calibration & drift ──────────────────────────────────────────────────────

@app.get("/api/calibration")
def calibration(domain: str = DomainQ) -> dict[str, Any]:
    r = rt(domain)
    corp = corpus(r.store)
    return {"corpus": corp[-150:], "n": len(corp),
            "coverage": empirical_coverage(r.store),
            "drift": check_drift(r.store).model_dump(),
            "alpha": CONFORMAL_ALPHA,
            "fidelity": r.store.kv_get("fidelity_ledger", [])[-60:]}


# ── transparency log ─────────────────────────────────────────────────────────

@app.get("/api/translog")
def get_translog(domain: str = DomainQ) -> dict[str, Any]:
    store = _store_for(domain)
    return {"entries": store.translog()[-200:],
            "root": translog.current_root(store),
            "chain": translog.verify_chain(store)}


@app.get("/api/translog/{idx}/proof")
def get_proof(idx: int, domain: str = DomainQ) -> dict[str, Any]:
    proof = translog.inclusion_proof(rt(domain).store, idx)
    if proof is None:
        raise HTTPException(404, "no such leaf")
    return proof


# ── evaluation ───────────────────────────────────────────────────────────────

@app.get("/api/eval/report")
def eval_report(domain: str = DomainQ) -> dict[str, Any]:
    rep = cached_report(rt(domain))
    return rep or {"status": "not_run"}


@app.post("/api/eval/run")
def eval_run(domain: str = DomainQ) -> dict[str, Any]:
    return run_replay(rt(domain))


# ── policy ───────────────────────────────────────────────────────────────────

@app.get("/api/policy")
def policy(domain: str = DomainQ) -> dict[str, Any]:
    r = rt(domain)
    return {"tiers": {k: v.__dict__ for k, v in AUTONOMY_TIERS.items()},
            "cmdp_limits": CMDP_LIMITS, "change_windows": CHANGE_WINDOWS,
            "autonomy": tenant_autonomy(r.store),
            "overrides": r.store.kv_get(OVERRIDES_KEY, {})}


@app.put("/api/policy/overrides")
def set_overrides(body: dict[str, Any] = Body(...),
                  domain: str = DomainQ) -> dict[str, Any]:
    r = rt(domain)
    ov = r.store.kv_get(OVERRIDES_KEY, {})
    ov.update({k: v for k, v in body.items() if k in ("max_tier",)})
    r.store.kv_set(OVERRIDES_KEY, ov)
    return {"overrides": ov}


# ── gateway: the runtime trust layer for agentic AI ─────────────────────────

@app.post("/api/gateway/agents")
def gw_register(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    acct = current_account(request, required=accounts.auth_required())
    profile = AgentProfile.model_validate(body)
    profile.owner_account = acct["account_id"]
    return gw.register_agent(profile).model_dump(by_alias=True)


@app.get("/api/gateway/agents")
def gw_agents(request: Request) -> list[dict[str, Any]]:
    acct = current_account(request, required=accounts.auth_required())
    scope = acct["account_id"] if accounts.auth_required() else None
    out = []
    for a in gw.list_agents(scope):
        classes = {}
        for cls in a.action_classes:
            conf = gw.confidence_for(a.agent_id, cls)
            classes[cls] = {"risk": a.action_classes[cls].risk,
                            "tier": gw.earned_tier(a.agent_id, cls),
                            "confidence": conf.model_dump()}
        out.append({**a.model_dump(by_alias=True), "calibration": classes})
    return out


@app.post("/api/gateway/check")
def gw_check(request: Request, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    acct = current_account(request, required=accounts.auth_required())
    req = ActionRequest.model_validate(body)
    # an agent may only act under an account that owns it (when auth required)
    if accounts.auth_required():
        owner = gw.agent_owner(req.agent_id)
        if owner and owner != acct["account_id"]:
            raise HTTPException(403, "agent belongs to another account")
    dec = gw.decide(req).model_dump()
    _notify_escalation(dec)
    return dec


@app.post("/api/gateway/outcome")
def gw_outcome(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    res = gw.record_outcome(ActionOutcome.model_validate(body))
    if "error" in res:
        raise HTTPException(404, res["error"])
    return res


def _own_agents(request: Request) -> tuple[dict, Optional[set]]:
    acct = current_account(request, required=accounts.auth_required())
    if not accounts.auth_required():
        return acct, None
    return acct, {a.agent_id for a in gw.list_agents(acct["account_id"])}


@app.get("/api/gateway/decisions")
def gw_decisions(request: Request, limit: int = 60) -> list[dict[str, Any]]:
    _, own = _own_agents(request)
    ds = gw.recent_decisions(limit if own is None else 500)
    ds = [d for d in ds if own is None or d.agent_id in own]
    return [d.model_dump() for d in ds[:limit]]


@app.get("/api/gateway/approvals")
def gw_approvals(request: Request) -> list[dict[str, Any]]:
    _, own = _own_agents(request)
    return [d.model_dump() for d in gw.pending_approvals()
            if own is None or d.agent_id in own]


@app.post("/api/gateway/approvals/{request_id}")
def gw_approve(request_id: str, request: Request,
               body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    acct, own = _own_agents(request)
    existing = gw.get_decision(request_id)
    if existing is None:
        raise HTTPException(404, "no such decision")
    if own is not None and existing.agent_id not in own:
        raise HTTPException(403, "not authorized to approve this decision")
    approver = acct.get("email") or body.get("by", "operator")
    dec = gw.approve(request_id, approver=approver,
                     allow=bool(body.get("allow", False)),
                     note=body.get("note", ""))
    if dec is None:
        raise HTTPException(404, "no pending escalation for that request")
    return dec.model_dump()


@app.get("/api/gateway/audit-pack")
def gw_audit_pack(request: Request, sample: int = 25) -> dict[str, Any]:
    from ..gateway.audit import build_audit_pack
    acct = current_account(request)
    if billing.has_feature(acct["account_id"], "evidence_export_full"):
        return build_audit_pack(sample_size=sample)
    # free tier: a capped, watermarked PREVIEW — full export is a Team feature
    pack = build_audit_pack(sample_size=3)
    pack["sampled_decisions"] = pack["sampled_decisions"][:3]
    pack["preview"] = True
    pack["upgrade"] = {"message": "Preview only — 3 of many decisions. "
                       "Upgrade to Team ($10/mo) for the full, schedulable, "
                       "auditor-ready evidence pack.", "url": "/app#/billing"}
    return pack


# ── interop ──────────────────────────────────────────────────────────────────

@app.get("/.well-known/agent-card.json")
def get_agent_card(request: Request) -> JSONResponse:
    return JSONResponse(agent_card(str(request.base_url).rstrip("/")))


@app.post("/a2a")
def a2a_rpc(body: dict[str, Any] = Body(...),
            domain: str = DomainQ) -> JSONResponse:
    return JSONResponse(handle_rpc(rt(domain), body))


@app.get("/api/schema/certificate")
def cert_schema() -> FileResponse:
    return FileResponse(SCHEMA_DIR / "keel-certificate-v1.json",
                        media_type="application/json")


# ── site + console ─────────────────────────────────────────────────────────

@app.get("/")
def landing() -> FileResponse:
    return FileResponse(SITE_DIR / "index.html")


@app.get("/docs")
def docs() -> FileResponse:
    return FileResponse(SITE_DIR / "docs.html")


@app.get("/app")
def console() -> FileResponse:
    return FileResponse(UI_DIR / "index.html")


app.mount("/site", StaticFiles(directory=str(SITE_DIR)), name="site")
app.mount("/ui", StaticFiles(directory=str(UI_DIR)), name="ui")
