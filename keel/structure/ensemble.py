"""Graph versioning + expert pin/veto.

Expert-pinned edges are a first-class input with provenance: domain operators
convert their knowledge into structure the statistics could not identify
(and veto edges the statistics hallucinated). Every graph is versioned; every
certificate references the exact version that produced it.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

from ..store import Store
from .discovery import DiscoveredEdge

PIN_KEY = "expert_pins"          # list of {src,dst,action:'pin'|'veto',by,reason}
CURRENT_KEY = "graph_current"
HISTORY_KEY = "graph_history"    # list of version ids, oldest first


def apply_expert_edits(edges: list[DiscoveredEdge],
                       pins: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = {(e.src_type, e.dst_type): e.to_row() for e in edges}
    for p in pins:
        key = (p["src"], p["dst"])
        if p["action"] == "veto":
            if key in rows:
                rows[key]["provenance"] = "expert_vetoed"
                rows[key]["pinned_by"] = p.get("by", "operator")
                rows[key]["pinned_reason"] = p.get("reason", "")
        elif p["action"] == "pin":
            row = rows.get(key) or {
                "src_type": p["src"], "dst_type": p["dst"], "strength": 0.5,
                "stability": 1.0, "lag_lo_ms": 1000, "lag_hi_ms": 60_000,
                "method": "expert",
            }
            row["provenance"] = "expert_pinned"
            row["pinned_by"] = p.get("by", "operator")
            row["pinned_reason"] = p.get("reason", "")
            rows[key] = row
    return list(rows.values())


def publish_graph(store: Store, edges: list[DiscoveredEdge]) -> str:
    pins = store.kv_get(PIN_KEY, [])
    rows = apply_expert_edits(edges, pins)
    active = [r for r in rows if r["provenance"] != "expert_vetoed"]
    digest = hashlib.sha256(
        repr(sorted((r["src_type"], r["dst_type"], round(r["strength"], 3))
                    for r in active)).encode()).hexdigest()[:8]
    version = f"G-{time.strftime('%Y%m%d')}-{digest}"
    store.put_causal_edges(version, rows)
    history = store.kv_get(HISTORY_KEY, [])
    if version not in history:
        history.append(version)
        store.kv_set(HISTORY_KEY, history)
    store.kv_set(CURRENT_KEY, version)
    return version


def current_graph(store: Store) -> tuple[str, list[dict[str, Any]]]:
    version = store.kv_get(CURRENT_KEY)
    if not version:
        return "", []
    return version, store.causal_edges(version)


def active_edges(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r["provenance"] != "expert_vetoed"]


def graph_edit_distance_norm(store: Store) -> float:
    """Normalized GED between the two latest graph versions (structural drift)."""
    history = store.kv_get(HISTORY_KEY, [])
    if len(history) < 2:
        return 0.0
    a = {(r["src_type"], r["dst_type"]) for r in
         active_edges(store.causal_edges(history[-2]))}
    b = {(r["src_type"], r["dst_type"]) for r in
         active_edges(store.causal_edges(history[-1]))}
    union = a | b
    return len(a ^ b) / max(len(union), 1)
