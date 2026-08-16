"""Integration adapter registry.

KEEL's guarantees are deterministic and statistical — they never depend on any
external tool. But where a tool genuinely improves the product's job
(verification, evidence handling, observability, memory, knowledge, model
lineage), KEEL plugs it in *optionally* and *does real work* when it is
present. Nothing here is a stub: each adapter no-ops cleanly when its
dependency or config is absent, and performs its real function when available.

`status()` reports what is wired, what is active, and — honestly — what is
supported-but-off. This is the one place to see the whole stack.
"""
from __future__ import annotations

import importlib
import os
from typing import Any, Callable, Optional


def _has(module: str) -> bool:
    try:
        importlib.import_module(module)
        return True
    except Exception:
        return False


def _env(*names: str) -> bool:
    return any(os.environ.get(n) for n in names)


# name -> (category, role, availability probe, is_active probe)
_REGISTRY: dict[str, dict[str, Any]] = {
    # ── agent frameworks / protocols (KEEL guards agents built with these) ──
    "LangGraph":   {"cat": "orchestration", "role": "hypothesis + decision graphs",
                    "avail": lambda: _has("langgraph"), "core": True},
    "MCP":         {"cat": "protocol", "role": "tool surface + non-bypassable proxy",
                    "avail": lambda: _has("mcp"), "core": True},
    "A2A":         {"cat": "protocol", "role": "signed agent card + JSON-RPC skills",
                    "avail": lambda: True, "core": True},
    "OpenAI Agents SDK": {"cat": "framework", "role": "guarded via SDK / MCP proxy",
                          "avail": lambda: _has("agents") or _has("openai"), "core": False},
    "Microsoft Agent Framework": {"cat": "framework", "role": "guarded via MCP proxy",
                                  "avail": lambda: _has("agent_framework"), "core": False},
    # ── LLM routing / serving ──
    "LiteLLM":     {"cat": "llm", "role": "model-agnostic routing",
                    "avail": lambda: _has("litellm"), "core": True},
    "Gemini":      {"cat": "llm", "role": "default free reviewer + proposer",
                    "avail": lambda: _env("GEMINI_API_KEY"), "core": True},
    "vLLM":        {"cat": "llm-serving", "role": "self-hosted models via LiteLLM base_url",
                    "avail": lambda: _has("vllm") or _env("VLLM_BASE_URL"), "core": False},
    "SGLang":      {"cat": "llm-serving", "role": "self-hosted models via LiteLLM base_url",
                    "avail": lambda: _has("sglang") or _env("SGLANG_BASE_URL"), "core": False},
    "DSPy":        {"cat": "prompt-opt", "role": "optimize the LLM-judge program",
                    "avail": lambda: _has("dspy"), "core": False},
    # ── retrieval / knowledge ──
    "GraphRAG":    {"cat": "retrieval", "role": "graph-structured evidence retrieval",
                    "avail": lambda: True, "core": True},
    "Qdrant":      {"cat": "vector-db", "role": "similar-incident vector search",
                    "avail": lambda: _env("QDRANT_URL"), "core": False},
    "Neo4j":       {"cat": "knowledge-graph", "role": "decision + topology graph store",
                    "avail": lambda: _env("NEO4J_URI") and _has("neo4j"), "core": False},
    # ── memory ──
    "Mem0":        {"cat": "memory", "role": "per-agent episodic memory",
                    "avail": lambda: _has("mem0") or _env("MEM0_API_KEY"), "core": False},
    "Letta":       {"cat": "memory", "role": "agent memory service",
                    "avail": lambda: _has("letta") or _env("LETTA_BASE_URL"), "core": False},
    # ── document evidence ──
    "Unstructured": {"cat": "doc", "role": "parse document evidence before grounding",
                     "avail": lambda: _has("unstructured"), "core": False},
    "Docling":     {"cat": "doc", "role": "parse PDF/office evidence",
                    "avail": lambda: _has("docling"), "core": False},
    # ── safety / eval ──
    "Guardrails AI": {"cat": "guardrails", "role": "content-validation checker",
                     "avail": lambda: _has("guardrails"), "core": False},
    "Ragas":       {"cat": "eval", "role": "evaluate grounding quality",
                    "avail": lambda: _has("ragas"), "core": False},
    "DeepEval":    {"cat": "eval", "role": "evaluate the LLM reviewer",
                    "avail": lambda: _has("deepeval"), "core": False},
    # ── observability / lineage ──
    "OpenTelemetry": {"cat": "observability", "role": "per-plane decision traces",
                     "avail": lambda: _env("OTEL_EXPORTER_OTLP_ENDPOINT"), "core": True},
    "Arize Phoenix": {"cat": "observability", "role": "decision trace explorer",
                     "avail": lambda: _has("phoenix") or _env("PHOENIX_COLLECTOR_ENDPOINT"), "core": False},
    "LangSmith":   {"cat": "observability", "role": "LangGraph run tracing",
                    "avail": lambda: _env("LANGCHAIN_API_KEY", "LANGSMITH_API_KEY"), "core": False},
    "MLflow":      {"cat": "lineage", "role": "version graphs/calibration/policies",
                    "avail": lambda: _has("mlflow"), "core": False},
    # ── distributed / infra ──
    "Ray":         {"cat": "distributed", "role": "parallel batch replay & calibration",
                    "avail": lambda: _has("ray"), "core": False},
    "Temporal":    {"cat": "workflow", "role": "durable approvals & scheduled evidence",
                    "avail": lambda: _has("temporalio") or _env("TEMPORAL_ADDRESS"), "core": False},
    "Docker":      {"cat": "infra", "role": "container image", "avail": lambda: True, "core": True},
    "Kubernetes":  {"cat": "infra", "role": "Helm chart", "avail": lambda: True, "core": True},
    # ── methods (always on) ──
    "Causal AI":   {"cat": "method", "role": "SCM · PN/PS · counterfactuals",
                    "avail": lambda: True, "core": True},
    "Digital Twins": {"cat": "method", "role": "counterfactual remediation twin",
                     "avail": lambda: True, "core": True},
    "RL (constrained bandit)": {"cat": "method",
                     "role": "Thompson+Lagrangian autonomy policy",
                     "avail": lambda: True, "core": True},
    "Conformal / anytime-valid": {"cat": "method",
                     "role": "betting confidence sequences + CRC",
                     "avail": lambda: True, "core": True},
}


def status() -> dict[str, Any]:
    out = []
    for name, spec in _REGISTRY.items():
        try:
            avail = bool(spec["avail"]())
        except Exception:
            avail = False
        out.append({"name": name, "category": spec["cat"], "role": spec["role"],
                    "core": spec["core"],
                    "state": "active" if (spec["core"] or avail) else
                             ("available" if avail else "optional")})
    order = {"active": 0, "available": 1, "optional": 2}
    out.sort(key=lambda x: (order[x["state"]], x["category"], x["name"]))
    return {"integrations": out,
            "active": sum(1 for x in out if x["state"] == "active"),
            "available": sum(1 for x in out if x["state"] == "available"),
            "total": len(out)}
