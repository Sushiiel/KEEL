"""Per-account settings must not be shared between tenants.

These settings lived in single global store keys. That meant any account could
read whether another had configured Slack, silently overwrite it, and — the
serious one — repoint every other tenant's escalation notifications at its own
webhook, exfiltrating agent ids, action classes and escalation reasons into an
attacker's workspace.
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("KEEL_SANDBOX", "1")
os.environ["KEEL_DATA_DIR"] = tempfile.mkdtemp(prefix="keel-tenantcfg-")
os.environ["KEEL_AUTH_REQUIRED"] = "1"

import pytest
from fastapi.testclient import TestClient

from keel import accounts, ratelimit
from keel.gateway import engine as gw
from keel.gateway.models import ActionClassSpec, AgentProfile
from keel.server import app as appmod
from keel.server.app import app

HOOK_A = "https://hooks.slack.com/services/AAA/AAA/aaaaaaaaaaaa"
HOOK_B = "https://hooks.slack.com/services/BBB/BBB/bbbbbbbbbbbb"


def _client_for(email: str) -> tuple[TestClient, dict]:
    acct = accounts.create_account(email, "password12345")
    c = TestClient(app)
    c.cookies.set("keel_session", accounts.issue_session(acct))
    return c, acct


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    monkeypatch.setattr(ratelimit, "_DISABLED", True)
    yield


@pytest.fixture(scope="module")
def tenants():
    a = _client_for("owner-a@example.com")
    b = _client_for("owner-b@example.com")
    return {"a": a, "b": b}


def test_slack_webhook_is_not_visible_to_another_account(tenants):
    (ca, _), (cb, _) = tenants["a"], tenants["b"]
    assert ca.put("/api/integrations/slack",
                  json={"webhook_url": HOOK_A}).status_code == 200
    seen_by_b = cb.get("/api/integrations").json()
    assert not seen_by_b["configured"].get("slack_webhook"), \
        "account B can see that A configured Slack"


def test_one_account_cannot_overwrite_anothers_webhook(tenants):
    (ca, _), (cb, _) = tenants["a"], tenants["b"]
    ca.put("/api/integrations/slack", json={"webhook_url": HOOK_A})
    cb.put("/api/integrations/slack", json={"webhook_url": HOOK_B})
    # A's setting must survive B writing its own
    assert ca.get("/api/integrations").json()["configured"]["slack_webhook"] is True
    store = gw.gw_store()
    _, acct_a = tenants["a"]
    _, acct_b = tenants["b"]
    cfg_a = store.kv_get(appmod._integrations_key(acct_a["account_id"]), {})
    cfg_b = store.kv_get(appmod._integrations_key(acct_b["account_id"]), {})
    assert cfg_a["slack_webhook"] == HOOK_A
    assert cfg_b["slack_webhook"] == HOOK_B


def test_escalation_notifies_only_the_agents_own_account(tenants, monkeypatch):
    """The exfiltration path: B must never receive A's escalation."""
    (ca, acct_a), (cb, acct_b) = tenants["a"], tenants["b"]
    ca.put("/api/integrations/slack", json={"webhook_url": HOOK_A})
    cb.put("/api/integrations/slack", json={"webhook_url": HOOK_B})

    gw.register_agent(AgentProfile(
        agent_id="agent-owned-by-a", name="a-agent",
        owner_account=acct_a["account_id"],
        action_classes={"send_email": ActionClassSpec(name="send_email")}))

    posted: list[str] = []

    class _FakeResponse:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, *a, **kw):
        posted.append(req.full_url)
        return _FakeResponse()

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    appmod._notify_escalation({"agent_id": "agent-owned-by-a",
                               "action_class": "send_email",
                               "decision": "ESCALATE",
                               "reasons": ["needs a human"]})
    assert posted == [HOOK_A], f"escalation went to the wrong webhook: {posted}"
    assert HOOK_B not in posted


def test_unattributed_agent_notifies_nobody(monkeypatch):
    """Fail closed: an agent with no owner must not fall back to some other
    account's webhook."""
    gw.register_agent(AgentProfile(
        agent_id="orphan-agent", name="orphan", owner_account="",
        action_classes={"send_email": ActionClassSpec(name="send_email")}))
    posted: list[str] = []
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, *a, **kw: posted.append(req.full_url))
    appmod._notify_escalation({"agent_id": "orphan-agent",
                               "action_class": "send_email",
                               "decision": "ESCALATE", "reasons": ["x"]})
    assert posted == []


def test_evidence_schedule_is_per_account(tenants):
    (ca, acct_a), (cb, acct_b) = tenants["a"], tenants["b"]
    ca.post("/api/gateway/schedule-evidence", json={"every_hours": 6, "sample": 10})
    cb.post("/api/gateway/schedule-evidence", json={"every_hours": 48, "sample": 99})
    store = gw.gw_store()
    sa = store.kv_get(appmod._schedules_key(acct_a["account_id"]), {})["evidence"]
    sb = store.kv_get(appmod._schedules_key(acct_b["account_id"]), {})["evidence"]
    assert sa["every_hours"] == 6 and sa["sample"] == 10
    assert sb["every_hours"] == 48 and sb["sample"] == 99


def test_key_mode_is_per_account(tenants):
    (ca, acct_a), (cb, acct_b) = tenants["a"], tenants["b"]
    ca.put("/api/security/key-mode", json={"mode": "hsm"})
    cb.put("/api/security/key-mode", json={"mode": "managed"})
    store = gw.gw_store()
    assert store.kv_get(appmod._key_mode_key(acct_a["account_id"])) == "hsm"
    assert store.kv_get(appmod._key_mode_key(acct_b["account_id"])) == "managed"


def test_webhook_url_is_never_returned_to_the_client(tenants):
    """It is a bearer credential for posting into a workspace."""
    ca, _ = tenants["a"]
    ca.put("/api/integrations/slack", json={"webhook_url": HOOK_A})
    assert HOOK_A not in ca.get("/api/integrations").text


def test_ssrf_guard_still_rejects_other_hosts(tenants):
    ca, _ = tenants["a"]
    for bad in ("http://hooks.slack.com/x",          # not https
                "https://evil.example.com/x",        # not allow-listed
                "https://169.254.169.254/latest"):   # cloud metadata
        r = ca.put("/api/integrations/slack", json={"webhook_url": bad})
        assert r.status_code == 400, f"{bad} was accepted"
