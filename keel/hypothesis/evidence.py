"""Evidence assembly: everything the hypothesizer may look at.

Four parallel retrievals, mirroring the production LangGraph fan-out:
topology subgraph at t0 (bi-temporal — never today's topology for yesterday's
incident), the deduplicated instance timeline, recent change events, and
similar historical incidents by type-set overlap.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import Event, Incident, TopoEdge
from ..store import Store
from ..substrate.vectors import embed, get_index, incident_tokens


@dataclass
class EvidencePack:
    instances: list[dict[str, Any]] = field(default_factory=list)
    topology: list[dict[str, Any]] = field(default_factory=list)
    changes: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    suppression: dict[str, Any] = field(default_factory=dict)
    layers: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"instances": self.instances, "topology": self.topology,
                "changes": self.changes, "history": self.history,
                "suppression": self.suppression, "layers": self.layers}


def dedupe_instances(events: list[Event]) -> list[dict[str, Any]]:
    firsts: dict[tuple[str, str], Event] = {}
    dup_count: dict[tuple[str, str], int] = {}
    for e in sorted(events, key=lambda x: x.ts):
        k = (e.entity_id, e.event_type)
        dup_count[k] = dup_count.get(k, 0) + 1
        if k not in firsts:
            firsts[k] = e
    return [{"variable": f"{e.entity_id}|{e.event_type}", "entity": e.entity_id,
             "type": e.event_type, "ts": e.ts, "severity": e.severity,
             "layer": e.raw.get("layer", ""), "alarms": dup_count[k],
             "info_gain": e.info_gain, "suppressed": e.suppressed}
            for k, e in sorted(firsts.items(), key=lambda kv: kv[1].ts)]


def topology_subgraph(topo: list[TopoEdge], entities: set[str]
                      ) -> list[dict[str, Any]]:
    return [{"src": e.src, "dst": e.dst, "relation": e.relation}
            for e in topo if e.src in entities or e.dst in entities]


def _incident_vector(store: Store, inc: Incident):
    evs = store.events_for(inc.incident_id)
    types = sorted({e.event_type for e in evs})
    layers = sorted({e.raw.get("layer", "") for e in evs if e.raw.get("layer")})
    return embed(incident_tokens(types, inc.entities, layers))


def ensure_history_index(store: Store, domain: str) -> None:
    """Populate the similar-incident vector index once per domain."""
    idx = get_index(domain)
    if getattr(idx, "ids", None) and len(idx.ids) > 0:
        return
    for h in store.incidents(limit=400):
        if h.status != "resolved":
            continue
        idx.upsert(h.incident_id, _incident_vector(store, h),
                   {"title": h.title, "root_cause": h.ground_truth,
                    "scenario": h.scenario})


def gather_evidence(store: Store, incident: Incident,
                    annotated: list[Event], domain: str = "telecom",
                    change_types: set[str] | None = None) -> EvidencePack:
    pack = EvidencePack()
    pack.instances = dedupe_instances(annotated)
    ents = set(incident.entities)
    topo = store.topology_at(incident.t0)
    pack.topology = topology_subgraph(topo, ents)
    pack.layers = sorted({i["layer"] for i in pack.instances if i["layer"]})

    total = len(annotated)
    kept = sum(1 for e in annotated if not e.suppressed)
    pack.suppression = {"alarms": total, "informative": kept,
                        "compression": round(1 - kept / total, 3) if total else 0.0}

    # change events in the prior 48h touching (or adjacent to) incident entities
    chtypes = change_types or {"cfg.push"}
    for ev in store.events_between(incident.t0 - 48 * 3600, incident.t0 + 60):
        if ev.event_type in chtypes and (ev.entity_id in ents or not ents):
            pack.changes.append({"entity": ev.entity_id, "ts": ev.ts, "type": ev.event_type,
                                 "hours_before": round((incident.t0 - ev.ts) / 3600, 1),
                                 "source": ev.raw.get("source", "")})

    # similar past incidents: vector search (hashed-feature embeddings;
    # local numpy index by default, Qdrant when QDRANT_URL is configured)
    ensure_history_index(store, domain)
    idx = get_index(domain)
    q = embed(incident_tokens([i["type"] for i in pack.instances],
                              incident.entities, pack.layers))
    pack.history = [{"incident_id": key, "similarity": round(sim, 3),
                     "title": meta.get("title", ""),
                     "root_cause": meta.get("root_cause"),
                     "scenario": meta.get("scenario", "")}
                    for key, sim, meta in idx.search(q, k=5,
                                                     exclude=incident.incident_id)]
    return pack
