"""KEEL bring-your-own-data quickstart.

This script plays the role of YOUR company — here, a fictional payments
processor with its own systems and its own alert vocabulary. Everything goes
through KEEL's public HTTP API exactly as your integration would:

  1. create a workspace
  2. upload topology (your systems + dependencies)
  3. upload historical events + labeled resolved incidents (your postmortems)
  4. declare what impact means in YOUR vocabulary
  5. learn: KEEL discovers your causal graph and calibrates on your outcomes
  6. stream a live burst  → KEEL detects the incident autonomously,
     verifies it, and issues a signed Causal Certificate

Run:  python examples/byo_quickstart.py [http://127.0.0.1:8347]
"""
from __future__ import annotations

import json
import random
import sys
import time
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8347"


def call(method: str, path: str, body=None):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


# ── 1 · workspace ────────────────────────────────────────────────────────────
ws = call("POST", "/api/workspaces", {
    "name": "Northbank Payments",
    "tenant": "northbank-prod",
    "profile": {
        "outage_types": ["payment_outage"],
        "degradation_types": ["checkout_latency_high"],
        "change_types": ["deploy_finished"],
        "hard_down_types": ["node_memory_pressure", "hsm_timeout"],
        "confounder_types": ["api_5xx_spike"],
        "runbooks": {
            "deploy_finished": ["rollback_deploy", "Roll back the release"],
            "db_conn_pool_exhausted": ["recycle_pool", "Recycle the connection pool"],
            "node_memory_pressure": ["drain_node", "Drain and reschedule the node"],
            "hsm_timeout": ["failover_hsm", "Fail over to the standby HSM"],
        },
        "action_dynamics": {
            "rollback_deploy": [3.0, 1.2, 30, True],
            "recycle_pool": [2.0, 0.8, 20, True],
            "drain_node": [9.0, 3.0, 120, True],
            "failover_hsm": [4.0, 1.5, 60, True],
        },
        "auto_verify": True,
        "gap_seconds": 300,
        "min_events": 4,
    }})
D = ws["key"]
print(f"[1] workspace created: {D}")

# ── 2 · topology (your systems, your names) ──────────────────────────────────
call("POST", f"/api/ingest/topology?domain={D}", {
    "entities": [
        {"entity_id": "k8s-node-a", "kind": "power", "layer": "infra", "site": "dc-east"},
        {"entity_id": "k8s-node-b", "kind": "power", "layer": "infra", "site": "dc-east"},
        {"entity_id": "pg-primary", "kind": "ne", "layer": "data", "site": "dc-east"},
        {"entity_id": "pg-replica", "kind": "ne", "layer": "data", "site": "dc-east"},
        {"entity_id": "redis-sessions", "kind": "ne", "layer": "data", "site": "dc-east"},
        {"entity_id": "api-gateway-1", "kind": "ne", "layer": "edge", "site": "dc-east"},
        {"entity_id": "api-gateway-2", "kind": "ne", "layer": "edge", "site": "dc-east"},
        {"entity_id": "auth-svc", "kind": "ne", "layer": "app", "site": "dc-east"},
        {"entity_id": "card-svc", "kind": "ne", "layer": "app", "site": "dc-east"},
        {"entity_id": "ledger-svc", "kind": "ne", "layer": "app", "site": "dc-east"},
        {"entity_id": "hsm-1", "kind": "ne", "layer": "security", "site": "dc-east"},
        {"entity_id": "checkout-payments", "kind": "service", "layer": "service",
         "attrs": {"customers": 2_100_000, "sla_class": "platinum", "paths": [
             ["api-gateway-1", "auth-svc", "card-svc", "pg-primary"],
             ["api-gateway-2", "auth-svc", "card-svc", "pg-replica"]]}},
    ],
    "edges": [
        {"src": "k8s-node-a", "dst": "auth-svc", "relation": "feeds"},
        {"src": "k8s-node-a", "dst": "card-svc", "relation": "feeds"},
        {"src": "k8s-node-b", "dst": "ledger-svc", "relation": "feeds"},
        {"src": "k8s-node-b", "dst": "pg-replica", "relation": "feeds"},
        {"src": "pg-primary", "dst": "card-svc", "relation": "carries"},
        {"src": "pg-primary", "dst": "ledger-svc", "relation": "carries"},
        {"src": "pg-replica", "dst": "ledger-svc", "relation": "carries"},
        {"src": "redis-sessions", "dst": "auth-svc", "relation": "carries"},
        {"src": "hsm-1", "dst": "auth-svc", "relation": "carries"},
        {"src": "auth-svc", "dst": "api-gateway-1", "relation": "carries"},
        {"src": "auth-svc", "dst": "api-gateway-2", "relation": "carries"},
        {"src": "card-svc", "dst": "api-gateway-1", "relation": "carries"},
        {"src": "card-svc", "dst": "api-gateway-2", "relation": "carries"},
        *[{"src": el, "dst": "checkout-payments", "relation": "serves"}
          for el in ["api-gateway-1", "api-gateway-2", "auth-svc", "card-svc",
                     "pg-primary", "pg-replica"]],
    ]})
print("[2] topology uploaded")

# ── 3 · historical events + labeled incidents (your postmortem archive) ──────
rng = random.Random(42)
now = time.time()
events, labels = [], []


def burst(t0: float, rows: list[tuple[float, str, str, int]]):
    for dt, ent, etype, sev in rows:
        events.append({"entity": ent, "type": etype, "severity": sev,
                       "ts": t0 + dt + rng.uniform(0, 3)})


for i in range(64):
    t0 = now - rng.uniform(2, 30) * 86400
    kind = rng.choice(["bad_deploy", "db_pool", "node_pressure", "hsm"])
    if kind == "bad_deploy":
        burst(t0, [(0, "card-svc", "deploy_finished", 4),
                   (rng.uniform(40, 120), "card-svc", "api_5xx_spike", 1),
                   (rng.uniform(100, 200), "api-gateway-1", "api_5xx_spike", 2),
                   (rng.uniform(150, 260), "card-svc", "card_decline_surge", 1),
                   (rng.uniform(200, 340), "checkout-payments", "payment_outage", 1)])
        root = ("card-svc", "deploy_finished")
    elif kind == "db_pool":
        burst(t0, [(0, "pg-primary", "db_conn_pool_exhausted", 1),
                   (rng.uniform(20, 60), "pg-primary", "db_replication_lag", 3),
                   (rng.uniform(30, 90), "card-svc", "api_latency_p99_high", 2),
                   (rng.uniform(40, 110), "ledger-svc", "api_latency_p99_high", 2),
                   (rng.uniform(90, 200), "checkout-payments", "checkout_latency_high", 2),
                   *( [(rng.uniform(240, 400), "checkout-payments", "payment_outage", 1)]
                      if rng.random() < 0.6 else [])])
        root = ("pg-primary", "db_conn_pool_exhausted")
    elif kind == "node_pressure":
        burst(t0, [(0, "k8s-node-a", "node_memory_pressure", 1),
                   (rng.uniform(10, 50), "auth-svc", "api_5xx_spike", 1),
                   (rng.uniform(15, 60), "card-svc", "api_5xx_spike", 1),
                   (rng.uniform(60, 160), "auth-svc", "auth_token_failures", 2),
                   (rng.uniform(120, 260), "checkout-payments", "payment_outage", 1)])
        root = ("k8s-node-a", "node_memory_pressure")
    else:
        burst(t0, [(0, "hsm-1", "hsm_timeout", 1),
                   (rng.uniform(15, 60), "auth-svc", "auth_token_failures", 1),
                   (rng.uniform(60, 150), "api-gateway-2", "api_5xx_spike", 2),
                   (rng.uniform(120, 240), "checkout-payments", "payment_outage", 1)])
        root = ("hsm-1", "hsm_timeout")
    # background noise every incident window
    for _ in range(rng.randint(1, 4)):
        events.append({"entity": rng.choice(["pg-replica", "redis-sessions",
                                             "api-gateway-2", "ledger-svc"]),
                       "type": rng.choice(["backup_job_slow", "cert_scan_info",
                                           "gc_pause_warn"]),
                       "severity": 4, "ts": t0 + rng.uniform(0, 400)})
    labels.append({"t0": t0 - 30, "t1": t0 + 460,
                   "root_cause_entity": root[0], "root_cause_type": root[1],
                   "title": f"{kind} incident ({time.strftime('%b %d', time.localtime(t0))})"})

r = call("POST", f"/api/ingest/events?domain={D}", events)
print(f"[3] history: {r['ingested']} events ingested")
r = call("POST", f"/api/ingest/incidents?domain={D}", labels)
print(f"    labeled incidents attached: {r['incidents']}")

# ── 4 · check KEEL's vocabulary suggestions (operator would confirm in UI) ───
sugg = call("GET", f"/api/workspaces/{D}/types")
print(f"[4] observed {len(sugg['types'])} event types; suggestions:",
      {t["type"]: t["suggest"] for t in sugg["types"] if t["suggest"]})

# ── 5 · learn ────────────────────────────────────────────────────────────────
r = call("POST", f"/api/learn?domain={D}")
print(f"[5] learned: graph {r['graph']} · calibration {r['calibration']}")

# ── 6 · a live incident streams in (no labels, no incident ids) ──────────────
t0 = now - 900          # closed 10 minutes ago → detector will pick it up
live = []
for dt, ent, etype, sev in [
        (0, "pg-primary", "db_conn_pool_exhausted", 1),
        (35, "pg-primary", "db_replication_lag", 3),
        (52, "card-svc", "api_latency_p99_high", 2),
        (66, "ledger-svc", "api_latency_p99_high", 2),
        (140, "checkout-payments", "checkout_latency_high", 2),
        (290, "checkout-payments", "payment_outage", 1),
        (300, "redis-sessions", "gc_pause_warn", 4)]:
    live.append({"entity": ent, "type": etype, "severity": sev, "ts": t0 + dt})
r = call("POST", f"/api/ingest/events?domain={D}", live)
watch = r["watch"]
print(f"[6] live burst streamed → detected {watch['detected']} · "
      f"auto-verified certs {watch['verified']}")

# ── 7 · read the certificate ─────────────────────────────────────────────────
if watch["verified"]:
    cert = call("GET", f"/api/certificates/{watch['verified'][0]}?domain={D}")
    c = cert["certificate"]
    pn = f"{c['pn']:.2f}" if c["pn"] is not None else f"[{c['pn_lo']},{c['pn_hi']}]"
    print(f"[7] CERTIFICATE {c['cert_id']}")
    print(f"    verdict {c['verdict']} · PN {pn} · claim {c['claim']['root_cause']}")
    print(f"    signature valid: {cert['verification']['signature_valid']} · "
          f"ledger leaf #{c['log_index']}")
    print(f"    decision: {c['decision']}")
else:
    print("[7] no auto-certificate — check /api/incidents and verify from the UI")
print(f"\nOpen {BASE} and switch the workspace selector to “Northbank Payments”.")
