# KEEL — The Runtime Trust Layer for Agentic AI

**Guardrails screen what agents say. Observability tells you what agents did.
KEEL decides whether this specific action executes at all — and hands you the
signed, tamper-evident, per-decision proof your auditor, insurer, and
regulator are already asking for.**

Companies are legally liable for what their agents do (Air Canada's chatbot
ruling; Replit's agent dropped a production DB during a code freeze and lied
about rollback; Gemini CLI destroyed user files; a $47K crypto agent fell to
prompt #482). Every existing layer screens text, observes after the fact, or
checks permissions. Nothing sits at the synchronous pre-action boundary with
per-deployment statistical calibration and signed evidence. KEEL does.

## The Agent Gateway (three lines to integrate)

```python
from keel.sdk import KeelGuard
guard = KeelGuard("http://127.0.0.1:8347", agent_id="support-bot")
guard.register(name="Support Bot", action_classes={
    "issue_refund": {"risk": "high", "budget_per_day": 500, "requires_evidence": True}})

@guard.protect("issue_refund", cost=lambda amount, **kw: amount)
def issue_refund(customer_id, amount): ...   # runs only on ALLOW / human approval
```

- **Shadow-first**: day one observes and signs everything, blocks nothing —
  except **tripwires**, a curated set of irreversible catastrophes (prod DB
  drop, recursive delete, funds transfer, credential export, mass send)
  hard-blocked in every mode. Zero false-positive risk, immediate audit value.
- **Citation integrity** (not "hallucination detection" — we name it
  honestly): claims must cite provided evidence; quotes must be substrings,
  numbers traceable; evidence is treated as untrusted input and screened for
  injection-shaped content. Grounded status never raises trust of high-risk actions.
- **Calibrated autonomy, earned per agent × action class**: marginal
  Clopper-Pearson lower bounds on success, from **externally-verified outcomes
  only** (agents cannot self-promote), with minimum-n gates. Low-risk track
  record never unlocks high-risk classes — trust-farming is structurally closed.
  The guarantee's exact scope is printed inside every certificate.
- **Human escalation queue** with recorded approver identity; **every decision
  is an Ed25519-signed certificate** in a Merkle transparency log — the
  evidence pack ISO 42001 auditors and AI-insurance underwriters sample.
- Deterministic path is sub-millisecond; the optional LLM judge runs only on
  high/critical risk and can only lower a decision, never raise it.

Proof: `python examples/gateway_quickstart.py` · UI at `#/gateway`.

**No mock data ships.** KEEL starts empty: register your agents or connect
your data. The four simulated industry demos exist for evaluation only,
behind an explicit flag: `KEEL_SANDBOX=1 python run.py`.
Honest limits are stated in-product: bounds are per-bucket marginal (never
per-decision), void under drift (detected + recalibrated), and citation
integrity verifies traceability, not truth.

## Also in the box: causal verification for operations

The same engine ships with a deep vertical: causal root-cause verification
for operational incidents (any industry, bring-your-own-data workspaces, four
sandbox demos) — PN/PS counterfactuals, conformal calibration, digital-twin
gating. See "Connect your data" in the UI.

## Install as a library

```bash
pip install keel          # or: pip install "keel[all]" for MCP/LLM/OTel/vectors

keel serve                # site, console, docs, gateway on :8347
keel serve --sandbox      # + simulated demo worlds (evaluation only)
keel guard proxy.json     # run the enforcing MCP proxy
```

Then open **http://localhost:8347** — the company site — with the operator
console at `/app` and full documentation at `/docs`. Data lives in
`~/.keel/data` (override with `KEEL_DATA_DIR`). No database or external
services required to start.

## Run from source

```bash
cd keel
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"
./.venv/bin/keel serve
```

## Bring your own data — any field, any company

The sandboxes are demos. The product is **Connect your data** (nav item 8, or
"＋ Connect your data…" in the workspace switcher):

1. **Create a workspace** — your name, your tenant.
2. **Connect topology** (paste JSON or POST it) — or skip it and KEEL infers
   adjacency from event co-occurrence.
3. **Stream events** — generic JSON/NDJSON at `POST /api/ingest/events`, or
   point Prometheus Alertmanager at `POST /api/webhook/alertmanager`.
4. **Upload labeled history** — your postmortems seed the calibration corpus;
   guarantees activate at 25 labeled incidents.
5. **Confirm vocabulary** — KEEL suggests which of YOUR event types mean
   outage / degradation / change / hard-down / hidden-confounder; you decide.
6. **Learn** — it discovers your causal graph, calibrates on your outcomes,
   and (with auto-verify on) detects live incident bursts autonomously and
   certifies them without a human in the loop. Label resolved incidents in
   the war room ("Close the loop") and the corpus — the moat — grows.

`examples/byo_quickstart.py` walks the whole flow over the HTTP API for a
fictional payments company; the same flow was validated end-to-end through
the UI alone for a rail operator. No simulator ever touches a workspace.

## Four industries in the sandbox — switch live in the UI

| Domain | Tenant | Canonical open incident | Shared-infra latent class |
|---|---|---|---|
| ⌁ **Telecom** metro transport | chennai-south-metro | 81-alarm optical→LDP→IS-IS→BGP cascade | site power feeds |
| ☁ **Cloud** SaaS platform (3 AZs) | meridian-commerce-prod | checkout SLO burn after a 02:00 deploy | Kubernetes nodes |
| ⚡ **Energy** distribution grid | coastal-grid-south | west feeder fault, 41k customers on backup | SCADA comms hubs |
| ⚙ **Manufacturing** gigafactory | helios-gigafactory | Model-X takt collapse at the paint conveyor | compressed-air plants |

A **domain pack** (`keel/domains/*.py`) supplies the world: entities, dependency
topology, event vocabulary, runbooks, resolver naming rules, and the hidden
generative cascade rules the demo simulator uses. The engine — discovery,
adjudication, calibration, twin, gate, certificates — is identical for all.
Packs follow a small **canonical impact schema** (`svc.*` impact types, `SVC:`
service entities, `cfg.push` change events, `power`-kind shared infrastructure)
— the same move as the certificate format: a vocabulary adapters map into, so
one verification engine serves every industry. Writing a fifth pack (hospital,
rail, logistics…) is one file.

## The modern stack — used where it earns its place

| Requested | How KEEL uses it |
|---|---|
| **MCP** | Real MCP server (`python -m keel.interop.mcp_server`): `verify_incident`, `get_certificate`, `execute_remediation`, … Register in Claude Code: `claude mcp add keel -- <path>/.venv/bin/python -m keel.interop.mcp_server` |
| **A2A** | Signed agent card at `/.well-known/agent-card.json` + JSON-RPC `/a2a`; any vendor's agent submits claims, receives signed certificates |
| **LangGraph** | The P3 hypothesis plane as a StateGraph (scope → parallel retrieval fan-out → hypothesize → validate, bounded re-query loop) when an LLM is configured; deterministic causal-frontier proposer otherwise |
| **LiteLLM** | `KEEL_LLM_MODEL` routes the proposer to any provider — Claude, GPT, Gemini, **Ollama/vLLM local** (telecom data-residency requirement). Swapping the LLM never moves the guarantees |
| **OpenTelemetry** | Set `OTEL_EXPORTER_OTLP_ENDPOINT` → every verification emits a trace with a span per plane (Jaeger/Tempo/Grafana-ready) |
| **Qdrant / vector RAG** | Similar-incident retrieval behind a pluggable index: in-process hashed-feature cosine by default, Qdrant via `QDRANT_URL` |
| **Guardrails / policy** | Pydantic hard schema boundary on every hypothesis (no free text enters adjudication), OPA-style declarative policy PDP, CMDP shield below the agent — enforcement under the model, per OWASP agentic guidance |
| **GraphRAG** | Evidence packs retrieve over the topology graph + incident history + change log (seeded-subgraph temporal variant, not whole-corpus indexing) |
| **Memory systems** | Episodic = incident corpus · semantic = versioned causal graph · procedural = runbooks + fidelity ledger · all per-tenant, all feeding calibration |
| **Docker / K8s / Helm / CI** | `Dockerfile`, `docker-compose.yml`, `deploy/helm/keel`, GitHub Actions |
| **DSPy / Ragas / Phoenix / MLflow** | Deliberately not wired: KEEL's evaluation is causal-localization replay (HR@k, conformal coverage, risk–coverage) against ground truth — a stronger, domain-true harness ships in `keel/evalx` |
| **Temporal / Kafka / Redis** | Deliberately deferred: the reference deployment is one process + SQLite so it runs anywhere in 10 s. The storage/bus interfaces are narrow by design; the spec's k3s/Redpanda topology is the scale-out path, not a demo dependency |

## Reference numbers (seeded deployments, held-out replay)

| | HR@1 KEEL | HR@1 corr+PageRank | Conformal coverage (nominal 0.90) |
|---|---|---|---|
| telecom | 0.82 | 0.33 | 0.83–0.97 by run |
| cloud | 0.94 | 0.00 | 0.94 |
| grid | 0.96 | 0.50 | 0.92 |
| manufacturing | 0.79 | 0.05 | 0.87 |

Abstention rates are **published** on the Evidence page (a system that says
"I don't know, and here's why" is the product). Entity resolution: precision
≥ 0.98 in all four domains, measured by a built-in harness. Verification
latency ≈ 0.2 s; certificates signed Ed25519 and anchored in a Merkle
transparency log with inclusion proofs.


## AI & ML stack

KEEL's guarantees are deterministic and statistical — the LLM only *proposes*,
it never adjudicates. Where a model helps, KEEL uses it, model-agnostically:

- **LLM (free by default): Google Gemini** — set `GEMINI_API_KEY` and
  `KEEL_LLM_MODEL=gemini-flash-latest`. Powers the optional gateway safety
  reviewer (advisory, may only *lower* a decision) and the causal
  hypothesis proposer. Swappable to Claude/GPT/Ollama/vLLM via LiteLLM.
- **LangGraph** — the hypothesis plane runs as a StateGraph (parallel
  retrieval fan-out → propose → validate) when an LLM is configured.
- **LangSmith** — set `LANGCHAIN_TRACING_V2=true` + `LANGCHAIN_API_KEY` to
  trace the LangGraph runs.
- **RAG** — similar-incident retrieval over a pluggable vector index
  (hashed-feature cosine locally; Qdrant via `QDRANT_URL`).
- **Frontier statistics** — anytime-valid confidence sequences, conformal
  risk control, Page-Hinkley drift detection, behavioral-anomaly Markov
  scoring, noisy-OR causal SCM with exact abduction.

Every one of these is optional and feature-flagged — the deterministic core
runs with none of them, and no guarantee depends on the model.

## Architecture

```
P7 Interop        MCP server · A2A agent card + JSON-RPC · open cert schema
P6 Actuation      blast radius · CMDP shield + projection · policy · tiers T0–T3
P5 Calibration    split/Mondrian conformal · drift gate (energy, GED, Brier fidelity)
P4 Adjudication   noisy-OR instance SCM · exact abduction · PN/PS · Tian–Pearl
                  bounds · placebo / common-cause / subset refuters
P3 Hypothesis     evidence pack (topology@t₀, changes, vector-retrieved history)
                  → LangGraph or causal-frontier proposer → hard schema boundary
P2 Structure      topology-constrained Hawkes discovery + stability selection
                  + orientation priors + same-type chain edges + expert pin/veto
P1 Substrate      measured entity resolution · bi-temporal topology · Hawkes
                  intensity + information-gain alarm suppression
─────────────────────────────────────────────────────────────────────────────
Domain packs      telecom · cloud · grid · manufacturing  (add yours: one file)
Certificates      Ed25519 canonical JSON → append-only Merkle transparency log
```

## Tests

```bash
./.venv/bin/python -m pytest tests/ -q     # 37 tests
# conformal coverage guarantees · Tian–Pearl bounds · PN on known SCMs ·
# abduction consistency · SCC cycle-breaking · Merkle tamper-evidence ·
# signature round-trip · fail-closed gate · per-pack schema integrity,
# canonical cascades, and resolver precision for all four industries
```

## Honesty invariants (enforced in code)

- Intervals, not point estimates, whenever identification fails.
- Abstention is a first-class verdict, always logged with a reason.
- Refutation results ride on the certificate; a claim that fails placebo
  refutation is REFUTED regardless of its PN.
- Bi-temporal everywhere: never certify a past incident against today's topology.
- Calibration and live scoring share one code path — no self-made
  exchangeability gap; twin fidelity is a Brier score with a minimum-sample
  guard, so one unlucky draw cannot poison the gate.
- The system fails **CLOSED**. Uncertain means blocked, not allowed.
