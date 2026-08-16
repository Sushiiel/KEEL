"""LangGraph hypothesis graph — the spec's P3, as a real StateGraph.

Shape (mirrors spec §10.2): scope → parallel retrieval fan-out (topology /
runbook-context / history / changes) → hypothesize (LLM, structured output)
→ validate → emit, with a bounded re-query loop when nothing valid came back.

Optional and feature-flagged: used when `langgraph` is importable AND an LLM
provider is configured; otherwise the deterministic causal-frontier proposer
runs and nothing changes. The graph never adjudicates — its output crosses the
same hard schema boundary as every other proposer.
"""
from __future__ import annotations

import json
from typing import Any, Optional, TypedDict

from ..models import Hypothesis, Intervention
from .evidence import EvidencePack
from .generator import MAX_HYPOTHESES, _PROMPT, llm_complete


class HypState(TypedDict, total=False):
    evidence: dict[str, Any]
    topology: list
    runbook_ctx: list
    history: list
    changes: list
    hypotheses: list[dict]
    rounds: int


def _available() -> bool:
    try:
        import langgraph  # noqa: F401
        return True
    except ImportError:
        return False


def langgraph_hypotheses(pack: EvidencePack, type_edges: list[dict]
                         ) -> Optional[list[Hypothesis]]:
    if not _available():
        return None
    from langgraph.graph import END, START, StateGraph

    valid_vars = {i["variable"] for i in pack.instances}

    def scope(state: HypState) -> dict:
        return {"rounds": 0, "evidence": {"instances": pack.instances[:60]}}

    # parallel retrieval fan-out — each node contributes one evidence facet
    def retrieve_topology(state: HypState) -> dict:
        return {"topology": pack.topology[:80]}

    def retrieve_history(state: HypState) -> dict:
        return {"history": pack.history}

    def retrieve_changes(state: HypState) -> dict:
        return {"changes": pack.changes[:10]}

    def hypothesize(state: HypState) -> dict:
        text = llm_complete(_PROMPT.format(
            k=MAX_HYPOTHESES,
            instances=json.dumps(state.get("evidence", {}).get("instances", [])),
            topology=json.dumps(state.get("topology", [])),
            changes=json.dumps(state.get("changes", [])),
            history=json.dumps(state.get("history", []))))
        raw: list[dict] = []
        if text:
            try:
                raw = json.loads(text[text.find("["):text.rfind("]") + 1])
            except Exception:
                raw = []
        # hard schema boundary: parse, validate, check variable existence
        valid = []
        for i, h in enumerate(raw[:MAX_HYPOTHESES]):
            try:
                hyp = Hypothesis(
                    hypothesis_id=h.get("hypothesis_id", f"h{i + 1}"),
                    intervention=Intervention(variable=h["variable"]),
                    mechanism=h.get("mechanism", ""),
                    predicted_path=h.get("predicted_path", []),
                    evidence_refs=h.get("evidence_refs", []),
                    prior_confidence=float(h.get("prior_confidence", 0.5)),
                    source="langgraph-proposer")
            except Exception:
                continue
            if hyp.intervention.variable in valid_vars:
                valid.append(hyp.model_dump())
        return {"hypotheses": valid, "rounds": state.get("rounds", 0) + 1}

    def need_more(state: HypState) -> str:
        if not state.get("hypotheses") and state.get("rounds", 0) < 2:
            return "hypothesize"                      # bounded re-query loop
        return END

    g = StateGraph(HypState)
    g.add_node("scope", scope)
    for name, fn in [("topo", retrieve_topology), ("history", retrieve_history),
                     ("changes", retrieve_changes)]:
        g.add_node(name, fn)
        g.add_edge("scope", name)
        g.add_edge(name, "hypothesize")
    g.add_node("hypothesize", hypothesize)
    g.add_conditional_edges("hypothesize", need_more)
    g.add_edge(START, "scope")

    final = g.compile().invoke({})
    hyps = [Hypothesis.model_validate(h) for h in final.get("hypotheses", [])]
    return hyps or None
