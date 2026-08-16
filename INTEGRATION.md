# Integrating KEEL with your product

Three calls: **register once → check before acting → report the outcome.**

## Pick your door
- Product has an **AI agent that acts** → Agent Gateway (below).
- Product **emits alerts/incidents** → UI "＋ Connect data" or POST
  `/api/ingest/events` — no code needed.

## Python (SDK)
```python
from keel.sdk import KeelGuard, GuardRejected
guard = KeelGuard("http://127.0.0.1:8347", agent_id="my-product-agent")
guard.register(name="My Product Agent", action_classes={
    "send_message": {"risk": "low"},
    "issue_refund": {"risk": "high", "budget_per_day": 500,
                     "requires_evidence": True}})

@guard.protect("issue_refund", cost=lambda amount, **kw: amount)
def issue_refund(customer_id, amount): ...   # runs only on ALLOW/approval
```
The wrapper auto-reports outcomes (return = success, exception = failure).

## Any language (REST)
1. `POST /api/gateway/agents`   {agent_id, name, action_classes:{...}}
2. `POST /api/gateway/check`    {agent_id, action_class, intent, payload,
   targets?, cost?, claims?, evidence?, reversible?} → {decision, request_id,
   reasons, cert_id}. Proceed only on ALLOW / SHADOW.
3. `POST /api/gateway/outcome`  {request_id, success, reported_by}

## MCP agents (zero code, non-bypassable)
`python -m keel.gateway.mcp_proxy --config proxy.json` — point the agent at
KEEL; KEEL holds the real tool servers and forwards only after ALLOW.

## Know this
- New agents default to **shadow mode**: everything recorded + signed, nothing
  blocked except tripwires. Set `"shadow_mode": false` to enforce.
- **ESCALATE** decisions wait in the UI Approval queue (Agent Gateway page).
  SDK option `wait_for_approval_s` can block until a human clicks.
- Trust (T2/T3 autonomy) grows **only** from externally-verified outcomes:
  use `reported_by: "webhook:..."` or `"human-review"` — self-reports never
  promote an agent.
- Deployed elsewhere? Replace 127.0.0.1:8347 with your server URL
  (`docker compose up` / Helm chart in `deploy/`).

## Guarding a real coding agent (Claude Code / Cursor)

Coding assistants are the most widely deployed agentic product (Copilot alone:
~90% of the Fortune 100) and the class behind the Replit/Gemini/Amazon-Q
incidents. Put yours behind KEEL in one command:

```bash
claude mcp add guarded-devtools -- \
  /path/to/keel/.venv/bin/python -m keel.gateway.mcp_proxy \
  --config /path/to/keel/examples/coding_agent_proxy.json
```

The agent now sees only KEEL-guarded tools. Shadow mode signs everything and
blocks only tripwires (rm -rf, DROP TABLE, funds transfer, credential export);
set "shadow_mode": false in the config to enforce risk tiers fully.
Live proof: `python examples/test_coding_agent.py`.
