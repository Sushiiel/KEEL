"""KEEL Gateway quickstart — put any AI agent behind the trust layer.

Three unrelated agents (support bot, DevOps agent, research assistant) run
their real lifecycle:  register → shadow-observe → tripwire saves →
outcome calibration → earned autonomy → human escalation queue.

Run:  python examples/gateway_quickstart.py [http://127.0.0.1:8347]
"""
from __future__ import annotations

import json
import sys
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8347"


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


# ── 1 · register three agents from three different products ─────────────────
call("POST", "/api/gateway/agents", {
    "agent_id": "support-bot", "name": "Support Bot", "framework": "langchain",
    "owner": "cx-team", "shadow_mode": False,
    "action_classes": {
        "send_reply": {"name": "send_reply", "risk": "low"},
        "issue_refund": {"name": "issue_refund", "risk": "high",
                         "budget_per_day": 500.0, "requires_evidence": True,
                         "schema": {"type": "object",
                                    "required": ["customer_id", "amount"],
                                    "properties": {"amount": {"type": "number"}}}}}})
call("POST", "/api/gateway/agents", {
    "agent_id": "devops-agent", "name": "DevOps Agent", "framework": "openai",
    "owner": "platform", "shadow_mode": True,          # day-one shadow mode
    "action_classes": {
        "run_sql": {"name": "run_sql", "risk": "high"},
        "restart_service": {"name": "restart_service", "risk": "medium",
                            "protected_targets": ["prod-payments*"]}}})
call("POST", "/api/gateway/agents", {
    "agent_id": "research-assistant", "name": "Research Assistant",
    "framework": "crewai", "owner": "insights", "shadow_mode": False,
    "action_classes": {
        "publish_summary": {"name": "publish_summary", "risk": "medium",
                            "requires_evidence": True}}})
print("[1] three agents registered (support-bot, devops-agent, research-assistant)")

# ── 2 · tripwire: the Replit-class catastrophe is blocked EVEN IN SHADOW ────
d = call("POST", "/api/gateway/check", {
    "agent_id": "devops-agent", "action_class": "run_sql",
    "intent": "clean up stale rows to fix the migration",
    "payload": {"sql": "DROP TABLE customer_invoices;", "db": "prod"},
    "targets": ["prod-db"], "reversible": False})
print(f"[2] shadow-mode agent, destructive SQL → {d['decision']}: {d['reasons'][0][:88]}")

# ── 3 · fabricated claim: citation-integrity catches invented numbers ───────
d = call("POST", "/api/gateway/check", {
    "agent_id": "research-assistant", "action_class": "publish_summary",
    "intent": "publish Q3 revenue summary to the exec channel",
    "payload": {"channel": "#exec"},
    "claims": [{"statement": "Q3 revenue grew 47% to $12.4M",
                "evidence_refs": ["report-q3"]}],
    "evidence": [{"ref": "report-q3",
                  "content": "Q3 revenue was $9.1M, up 18% from Q2."}]})
print(f"[3] fabricated numbers in claim → {d['decision']}: "
      f"{[c['detail'][:70] for c in d['checks'] if c['checker'] == 'citation_integrity']}")

# ── 4 · grounded claim, but HIGH risk with no track record → human queue ────
d = call("POST", "/api/gateway/check", {
    "agent_id": "support-bot", "action_class": "issue_refund",
    "intent": "refund duplicate charge per order evidence",
    "payload": {"customer_id": "cus_991", "amount": 49.0},
    "targets": ["cus_991"], "cost": 49.0,
    "claims": [{"statement": "customer was charged 49.0 twice on the same day",
                "evidence_refs": ["stripe-evt"]}],
    "evidence": [{"ref": "stripe-evt",
                  "content": "charges: 49.0 at 09:14, 49.0 at 09:14 — duplicate "
                             "idempotency failure for cus_991"}]})
esc_id = d["request_id"]
print(f"[4] grounded refund, cold start → {d['decision']} (queued for human)")

# ── 5 · a human approves it from the queue; outcome closes the loop ─────────
call("POST", f"/api/gateway/approvals/{esc_id}", {"allow": True, "by": "cx-lead",
                                                  "note": "verified in Stripe"})
call("POST", "/api/gateway/outcome", {"request_id": esc_id, "success": True,
                                      "reported_by": "webhook:stripe"})
print("[5] human approved · outcome recorded from an EXTERNAL signal (webhook)")

# ── 6 · autonomy is earned: 12 externally-verified low-risk successes ───────
for i in range(12):
    d = call("POST", "/api/gateway/check", {
        "agent_id": "support-bot", "action_class": "send_reply",
        "intent": f"reply to ticket {1000 + i}", "payload": {"ticket": 1000 + i}})
    if d["decision"] == "ALLOW":
        call("POST", "/api/gateway/outcome",
             {"request_id": d["request_id"], "success": True,
              "reported_by": "human-review"})
agents = call("GET", "/api/gateway/agents")
sb = next(a for a in agents if a["agent_id"] == "support-bot")
cal = sb["calibration"]["send_reply"]
print(f"[6] send_reply after 12 external outcomes → tier T{cal['tier']}, "
      f"p_lower={cal['confidence']['p_lower']} (n={cal['confidence']['n']})")

# ── 7 · but cheap track record NEVER unlocks the high-risk class ────────────
ref = sb["calibration"]["issue_refund"]
print(f"[7] issue_refund is still tier T{ref['tier']} with n={ref['confidence']['n']} "
      "— low-risk history cannot promote high-risk autonomy (trust-farming closed)")

# ── 8 · every decision is a signed certificate in the Merkle ledger ─────────
led = call("GET", "/api/translog?domain=gateway")
print(f"[8] gateway ledger: {led['chain']['size']} signed decisions · "
      f"chain consistent: {led['chain']['consistent']} · root {led['root'][:20]}…")
# ── 9 · leave one decision waiting for YOU in the approval queue ────────────
call("POST", "/api/gateway/check", {
    "agent_id": "support-bot", "action_class": "issue_refund",
    "intent": "refund late-delivery compensation per policy",
    "payload": {"customer_id": "cus_1204", "amount": 75.0},
    "targets": ["cus_1204"], "cost": 75.0,
    "claims": [{"statement": "order arrived 6 days late; policy grants 75.0 compensation",
                "evidence_refs": ["order-log"]}],
    "evidence": [{"ref": "order-log",
                  "content": "order #88231 for cus_1204: promised day 2, delivered "
                             "day 8 (6 days late). policy tier: 75.0 compensation"}]})
print("[9] one refund left WAITING in the approval queue — go press Approve in the UI")
print(f"\nOpen {BASE}/#/gateway to see agents, live decisions, and the approval queue.")
