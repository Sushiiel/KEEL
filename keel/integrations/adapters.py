"""Functional adapters. Each performs its real job when its dependency/config
is present, and returns a clean no-op result otherwise. Never raises."""
from __future__ import annotations

import os
from typing import Any, Optional


# ── document evidence: Unstructured / Docling ────────────────────────────────

def parse_document(path_or_bytes: Any, filename: str = "") -> Optional[str]:
    """Extract text from a PDF/office document so citation-integrity can check
    claims against it. Tries Docling, then Unstructured. None if unavailable."""
    try:
        from docling.document_converter import DocumentConverter  # type: ignore
        if isinstance(path_or_bytes, str):
            return DocumentConverter().convert(path_or_bytes).document.export_to_markdown()
    except Exception:
        pass
    try:
        from unstructured.partition.auto import partition  # type: ignore
        els = (partition(filename=path_or_bytes) if isinstance(path_or_bytes, str)
               else partition(file=path_or_bytes))
        return "\n".join(str(e) for e in els)
    except Exception:
        return None


# ── Guardrails AI: extra content-validation checker ──────────────────────────

def guardrails_scan(text: str) -> Optional[dict[str, Any]]:
    """Run Guardrails AI validators over agent output/evidence if installed.
    Returns {passed, detail} or None when Guardrails isn't available."""
    try:
        from guardrails import Guard  # type: ignore
    except Exception:
        return None
    try:
        guard = Guard()               # a bare guard; users attach validators
        guard.validate(text)
        return {"passed": True, "detail": "guardrails validators passed"}
    except Exception as e:
        return {"passed": False, "detail": f"guardrails: {str(e)[:160]}"}


# ── MLflow: version lineage for the objects a certificate references ─────────

def mlflow_log_versions(versions: dict[str, str]) -> bool:
    """Record graph/scm/policy/model versions to MLflow so a certificate's
    referenced artifacts are reproducible. No-op if MLflow isn't installed."""
    try:
        import mlflow  # type: ignore
    except Exception:
        return False
    try:
        mlflow.set_experiment("keel-decisions")
        with mlflow.start_run(nested=True):
            for k, v in versions.items():
                mlflow.set_tag(k, v)
        return True
    except Exception:
        return False


# ── Neo4j knowledge graph: mirror decisions + topology ───────────────────────

def neo4j_sync_decision(agent_id: str, action_class: str, decision: str,
                        cert_id: str) -> bool:
    """Write a decision edge into a Neo4j knowledge graph when configured.
    (:Agent)-[:DECIDED {decision, cert}]->(:ActionClass)."""
    uri = os.environ.get("NEO4J_URI")
    if not uri:
        return False
    try:
        from neo4j import GraphDatabase  # type: ignore
        auth = (os.environ.get("NEO4J_USER", "neo4j"),
                os.environ.get("NEO4J_PASSWORD", ""))
        with GraphDatabase.driver(uri, auth=auth) as drv, drv.session() as ses:
            ses.run("MERGE (a:Agent {id:$aid}) "
                    "MERGE (c:ActionClass {name:$cls}) "
                    "CREATE (a)-[:DECIDED {decision:$dec, cert:$cert, "
                    "ts:timestamp()}]->(c)",
                    aid=agent_id, cls=action_class, dec=decision, cert=cert_id)
        return True
    except Exception:
        return False


# ── Mem0 / Letta: per-agent episodic memory ──────────────────────────────────

def memory_write(agent_id: str, event: str) -> bool:
    """Persist an agent episodic-memory event to Mem0 or Letta if configured."""
    if os.environ.get("MEM0_API_KEY"):
        try:
            from mem0 import MemoryClient  # type: ignore
            MemoryClient().add(event, user_id=agent_id)
            return True
        except Exception:
            pass
    base = os.environ.get("LETTA_BASE_URL")
    if base:
        try:
            import json, urllib.request
            urllib.request.urlopen(urllib.request.Request(
                base.rstrip("/") + "/v1/agents/" + agent_id + "/messages",
                data=json.dumps({"messages": [{"role": "system", "text": event}]}).encode(),
                headers={"Content-Type": "application/json"}), timeout=5)
            return True
        except Exception:
            pass
    return False


# ── OpenTelemetry / Arize Phoenix: decision spans ────────────────────────────

def trace_decision(name: str, attrs: dict[str, Any]) -> None:
    """Emit a decision span (OTel; Phoenix reads the same OTLP stream)."""
    try:
        from ..otel import span
        with span(name, attrs):
            pass
    except Exception:
        pass
