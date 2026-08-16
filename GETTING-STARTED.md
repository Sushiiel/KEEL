# KEEL — Getting Started (plain English)

## What this is

AI agents don't just chat anymore — they *act*: send emails, issue refunds,
run commands, change data. When they act wrongly, **your company** pays for it
(real cases: Air Canada's chatbot ruling, Replit's deleted production DB).

KEEL is the **security guard + notary between your AI and the real world**:

1. Every action is checked **before** it happens.
2. Catastrophes (DB drops, money transfers, credential exports) are **always
   blocked** — the "tripwires", active even on day one.
3. Risky actions wait for a **human approval** (name goes on the record).
4. The AI **earns trust** per action type from verified outcomes — being good
   at replies never unlocks moving money.
5. Every decision gets a **signed, tamper-proof receipt** — proof for audits,
   lawyers, insurers ("Export audit evidence pack" button).
6. Claims are checked against cited evidence — invented numbers get blocked.

## Start it

```bash
cd keel
./.venv/bin/python run.py        # → http://127.0.0.1:8347
```

It starts **empty** — no demo data, by design.

## See it work (30 seconds)

```bash
./.venv/bin/python examples/gateway_quickstart.py
```

Then open **Agent Gateway** (menu item 9): one agent blocked for dangerous
SQL, one blocked for inventing numbers, one waiting in the approval queue —
click **Approve**.

## Protect your own AI (3 lines)

```python
from keel.sdk import KeelGuard
guard = KeelGuard("http://127.0.0.1:8347", agent_id="my-bot")
guard.register(name="My Bot", action_classes={
    "issue_refund": {"risk": "high", "budget_per_day": 500}})

@guard.protect("issue_refund", cost=lambda amount, **kw: amount)
def issue_refund(customer_id, amount): ...   # runs only if KEEL says yes
```

New agents start in **shadow mode**: everything is recorded and signed,
nothing is blocked except tripwires. Report outcomes (or let the wrapper do
it) and trust builds automatically.

Non-bypassable mode for MCP-based agents (the agent physically cannot skip
the check): `python -m keel.gateway.mcp_proxy --config proxy.json`.

## The words on screen

| Word | Meaning |
|---|---|
| ALLOW / BLOCK / ESCALATE / SHADOW | ran · refused · waiting for a human · observed only |
| Tripwire | always-on alarm for irreversible disasters |
| T1 / T2 / T3 | trust earned: needs approval → acts + notifies → acts silently |
| p⩾0.83 · n=12 | from 12 verified outcomes: statistically at least 83% success floor |
| Certificate / Ledger | the signed receipt · the tamper-proof chain of all receipts |

## The other door: "Connect data" (menu item 8)

If you have systems that produce alerts (IT, factory, grid, anything), paste
your alerts + past incidents there. KEEL learns how failures spread in YOUR
world and, on each new incident, names the most likely root cause with honest
confidence — including "I don't know" when the evidence is thin.

## Want demo worlds to explore first?

```bash
KEEL_SANDBOX=1 ./.venv/bin/python run.py
```

Four simulated industries appear in the top-right menu. Off by default.
