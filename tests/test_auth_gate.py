"""Deny-by-default auth: EVERY /api route must require auth in production.

This is the regression guard for the launch audit's blocker finding — that
auth was opt-in per handler, leaving ~34 endpoints anonymous.
"""
import os, tempfile
os.environ["KEEL_AUTH_REQUIRED"] = "1"
os.environ.setdefault("KEEL_SANDBOX", "1")
os.environ["KEEL_DATA_DIR"] = tempfile.mkdtemp(prefix="keel-gate-")

from fastapi.testclient import TestClient
from keel.server.app import app, _PUBLIC_EXACT, _PUBLIC_PREFIXES

client = TestClient(app)


def _sample_path(route_path: str) -> str:
    """Fill path params with a dummy so the route actually matches."""
    out = route_path
    for token in ("{cert_id}", "{incident_id}", "{request_id}", "{key}", "{idx}"):
        out = out.replace(token, "x")
    return out


def test_every_api_route_requires_auth():
    """No anonymous access to anything under /api or /a2a except the
    explicitly public list."""
    leaked = []
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if not (path.startswith("/api/") or path == "/a2a"):
            continue
        if path in _PUBLIC_EXACT:
            continue
        target = _sample_path(path)
        for method in ("GET", "POST", "PUT", "DELETE"):
            if method not in methods:
                continue
            r = client.request(method, target, json={})
            if r.status_code != 401:
                leaked.append(f"{method} {path} → {r.status_code}")
    assert not leaked, "UNAUTHENTICATED ENDPOINTS:\n  " + "\n  ".join(leaked)


def test_the_specific_exploits_are_closed():
    """The exact attacks the audit demonstrated."""
    assert client.post("/api/workspaces", json={"name": "x"}).status_code == 401
    assert client.put("/api/policy/overrides", json={"max_tier": 3}).status_code == 401
    assert client.post("/api/certificates/x/execute", json={"force": True}).status_code == 401
    assert client.post("/api/gateway/outcome", json={}).status_code == 401
    assert client.post("/api/ingest/events", json=[]).status_code == 401
    assert client.post("/a2a", json={}).status_code == 401
    assert client.get("/api/integrations/status").status_code == 401


def test_public_surface_still_works():
    """Sign-in, the marketing pages, health, and the open standards stay open."""
    for path in ("/", "/docs", "/app", "/healthz", "/favicon.ico",
                 "/robots.txt", "/api/auth/config",
                 "/api/schema/certificate", "/.well-known/agent-card.json"):
        assert client.get(path).status_code == 200, path


def test_api_explorer_disabled_in_production():
    assert client.get("/openapi.json").status_code in (401, 404)
    assert client.get("/api-explorer").status_code in (401, 404)


def test_security_headers_present():
    h = client.get("/").headers
    assert h["X-Frame-Options"] == "DENY"
    assert h["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in h["Content-Security-Policy"]
