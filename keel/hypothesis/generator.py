"""Hypothesis generation. The LLM proposes; it never adjudicates.

Two proposers share one contract — every hypothesis must (a) validate against
the Pydantic schema and (b) reference a variable that exists in the incident's
evidence scope. Anything else is discarded before adjudication. No free-text
hypotheses, no exceptions.

  causal-frontier (default, deterministic): candidate roots are observed
  instances with no observed causal parent preceding them ("frontier" of the
  cascade), ranked by earliest onset, causal depth to the outage, severity,
  and change-correlation.

  claude (optional): if ANTHROPIC_API_KEY is set and the `anthropic` package
  is installed, Claude reads the evidence pack and proposes structured
  hypotheses; invalid ones are dropped and the frontier proposer backfills.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from typing import Any, Optional

from pydantic import ValidationError

from ..models import Hypothesis, Intervention
from .evidence import EvidencePack

MAX_HYPOTHESES = 5


def _type_children(type_edges: list[dict]) -> dict[str, list[str]]:
    ch: dict[str, list[str]] = defaultdict(list)
    for r in type_edges:
        ch[r["src_type"]].append(r["dst_type"])
    return ch


def _mechanism_path(type_edges: list[dict], start: str,
                    goals: set[str] | None = None) -> list[str]:
    """Shortest chain start -> ... -> any impact type in the learned graph."""
    goals = goals or {"svc.sla_breach"}
    ch = _type_children(type_edges)
    frontier = [[start]]
    seen = {start}
    while frontier:
        path = frontier.pop(0)
        if path[-1] in goals:
            return path
        for nxt in ch.get(path[-1], []):
            if nxt not in seen and len(path) < 8:
                seen.add(nxt)
                frontier.append(path + [nxt])
    return [start]


def frontier_hypotheses(pack: EvidencePack, type_edges: list[dict],
                        impact_types: set[str] | None = None,
                        change_types: set[str] | None = None
                        ) -> list[Hypothesis]:
    impact = impact_types or set()
    changes = change_types or {"cfg.push"}
    parents_of: dict[str, set[str]] = defaultdict(set)
    for r in type_edges:
        parents_of[r["dst_type"]].add(r["src_type"])
    known_types = {r["src_type"] for r in type_edges} | set(parents_of)

    # suppression filters alarm noise for humans; candidate generation spans
    # every observed instance — a suppressed alarm can still be the root
    observed = list(pack.instances)
    observed_types_before: dict[str, set[str]] = {}
    seen_types: set[str] = set()
    for inst in observed:
        observed_types_before[inst["variable"]] = set(seen_types)
        seen_types.add(inst["type"])

    candidates: list[tuple[float, dict]] = []
    for inst in observed:
        t = inst["type"]
        if t.startswith("svc.") or t in impact:
            continue                       # symptoms, not causes
        if t not in known_types:
            continue                       # not a variable of the causal model
        has_observed_parent = bool(parents_of.get(t, set())
                                   & observed_types_before[inst["variable"]])
        depth = len(_mechanism_path(type_edges, t, goals=impact or None)) - 1
        onset_rank = observed.index(inst) / max(len(observed), 1)
        score = (2.0 * (not has_observed_parent) + 0.9 * min(depth, 6) / 6
                 + 0.8 * (1 - onset_rank) + 0.3 * (5 - inst["severity"]) / 4)
        candidates.append((score, inst))

    candidates.sort(key=lambda x: -x[0])
    out: list[Hypothesis] = []
    top = candidates[0][0] if candidates else 1.0
    for rank, (score, inst) in enumerate(candidates[:MAX_HYPOTHESES]):
        chain = _mechanism_path(type_edges, inst["type"], goals=impact or None)
        out.append(Hypothesis(
            hypothesis_id=f"h{rank + 1}",
            intervention=Intervention(variable=inst["variable"], set_to="nominal"),
            mechanism=" → ".join(chain),
            predicted_path=[inst["entity"]],
            evidence_refs=[inst["variable"]],
            prior_confidence=round(min(0.95, 0.4 + 0.55 * score / max(top, 1e-6)), 3),
            source="keel-frontier"))

    # change-correlated candidate, if a recent push touched the incident scope
    change_vars = {f"{c['entity']}|{c.get('type', 'cfg.push')}" for c in pack.changes}
    have = {h.intervention.variable for h in out}
    for var in sorted(change_vars - have):
        if any(i["variable"] == var for i in pack.instances):
            ctype = var.split("|", 1)[1]
            out = out[:MAX_HYPOTHESES - 1] + [Hypothesis(
                hypothesis_id=f"h{len(out) + 1}",
                intervention=Intervention(variable=var, set_to="rolled_back"),
                mechanism=" → ".join(_mechanism_path(type_edges, ctype,
                                                     goals=impact or None)),
                predicted_path=[var.split("|")[0]],
                evidence_refs=[var],
                prior_confidence=0.55, source="keel-change-agent")]
            break
    return out


# ── optional Claude proposer ─────────────────────────────────────────────────

_PROMPT = """You are the hypothesis-generation agent inside KEEL, a causal
verification system for telecom operations. Given the evidence below, propose
up to {k} candidate root-cause hypotheses as JSON. You PROPOSE; a causal
engine adjudicates. Each hypothesis must use a "variable" copied EXACTLY from
the instance list. Reply with ONLY a JSON array of objects with keys:
hypothesis_id, variable, mechanism, predicted_path (list of entity ids),
evidence_refs (list of variables), prior_confidence (0..1).

EVIDENCE
Instances (chronological): {instances}
Topology edges: {topology}
Recent changes: {changes}
Similar past incidents: {history}
"""


def _gemini_complete(prompt: str, max_tokens: int) -> Optional[str]:
    """Google Gemini via the free Generative Language API (pure stdlib)."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return None
    import json as _json, urllib.error, urllib.request
    model = os.environ.get("KEEL_LLM_MODEL", "gemini-flash-latest")
    model = model.split("/")[-1]                 # tolerate 'gemini/xxx'
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent")
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens}}
    import time as _t
    for attempt in range(3):
        req = urllib.request.Request(url, data=_json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              "X-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = _json.loads(r.read())
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(pt.get("text", "") for pt in parts) or None
        except urllib.error.HTTPError as e:
            if e.code in (503, 429) and attempt < 2:
                _t.sleep(1.5 * (attempt + 1))    # transient overload — back off
                continue
            return None
        except Exception:
            return None
    return None


def llm_complete(prompt: str, max_tokens: int = 1500) -> Optional[str]:
    """Model-agnostic completion. Prefers Google Gemini (free) when
    GEMINI_API_KEY is set; else LiteLLM routes to any provider named by
    KEEL_LLM_MODEL (claude/gpt/ollama/vllm); else the Anthropic SDK. Returns
    None when nothing is configured. Swapping the LLM never moves the
    guarantees — adjudication lives outside the model."""
    g = _gemini_complete(prompt, max_tokens)
    if g is not None:
        return g
    model = os.environ.get("KEEL_LLM_MODEL", "claude-sonnet-5")
    if model.startswith("gemini"):
        return None                              # gemini requested but no key
    try:
        import litellm  # type: ignore
        resp = litellm.completion(model=model, max_tokens=max_tokens,
                                  messages=[{"role": "user", "content": prompt}])
        return resp.choices[0].message.content
    except Exception:
        pass
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic  # type: ignore
            msg = anthropic.Anthropic().messages.create(
                model=model if model.startswith("claude") else "claude-sonnet-5",
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}])
            return msg.content[0].text
        except Exception:
            return None
    return None


def _llm_configured() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY")
                or os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("OPENAI_API_KEY")
                or os.environ.get("KEEL_LLM_MODEL", "").startswith(("ollama", "vllm")))


def claude_hypotheses(pack: EvidencePack, type_edges: list[dict]
                      ) -> Optional[list[Hypothesis]]:
    if not _llm_configured():
        return None
    text = llm_complete(_PROMPT.format(
        k=MAX_HYPOTHESES,
        instances=json.dumps(pack.instances[:60]),
        topology=json.dumps(pack.topology[:80]),
        changes=json.dumps(pack.changes[:10]),
        history=json.dumps(pack.history)))
    if not text:
        return None
    try:
        start, end = text.find("["), text.rfind("]") + 1
        raw: list[dict[str, Any]] = json.loads(text[start:end])
    except Exception:
        return None

    valid_vars = {i["variable"] for i in pack.instances}
    out: list[Hypothesis] = []
    for i, h in enumerate(raw[:MAX_HYPOTHESES]):
        try:
            hyp = Hypothesis(
                hypothesis_id=h.get("hypothesis_id", f"h{i + 1}"),
                intervention=Intervention(variable=h["variable"], set_to="nominal"),
                mechanism=h.get("mechanism", ""),
                predicted_path=h.get("predicted_path", []),
                evidence_refs=h.get("evidence_refs", []),
                prior_confidence=float(h.get("prior_confidence", 0.5)),
                source="llm-proposer")
        except (ValidationError, KeyError, TypeError, ValueError):
            continue
        if hyp.intervention.variable in valid_vars:     # hard schema boundary
            out.append(hyp)
    return out or None


def generate_hypotheses(pack: EvidencePack, type_edges: list[dict],
                        impact_types: set[str] | None = None,
                        change_types: set[str] | None = None
                        ) -> tuple[list[Hypothesis], str]:
    llm = None
    generator = "llm"
    if _llm_configured():
        try:
            from .langgraph_agent import langgraph_hypotheses
            llm = langgraph_hypotheses(pack, type_edges)
            generator = "langgraph"
        except ImportError:
            llm = None
        if llm is None:
            llm = claude_hypotheses(pack, type_edges)
            generator = "llm"
    if llm:
        have = {h.intervention.variable for h in llm}
        for h in frontier_hypotheses(pack, type_edges, impact_types, change_types):
            if len(llm) >= MAX_HYPOTHESES:
                break
            if h.intervention.variable not in have:
                llm.append(h)
        return llm, f"{generator}+frontier"
    return (frontier_hypotheses(pack, type_edges, impact_types, change_types),
            "causal-frontier")
