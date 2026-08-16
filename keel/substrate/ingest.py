"""Ingestion: the customer's data becomes the world.

Everything a deployment needs arrives here, in the customer's own vocabulary:

  topology   entities + dependency edges (JSON) — or inferred from event
             co-occurrence when none exists
  events     alarms/alerts/logs as generic JSON or NDJSON, or via the
             Prometheus Alertmanager webhook
  history    labeled resolved incidents (windows + root cause) — the seed of
             the calibration corpus, the scarcest and most valuable input

Incidents are detected autonomously from the raw stream (burst sessionization:
events joined while gaps stay under the workspace's gap_seconds; a window
becomes an incident at min_events). Impact/change/hard-down vocabulary is
whatever the customer declares in their workspace profile — KEEL suggests
candidates from the observed stream, the operator confirms.
"""
from __future__ import annotations

import re
import time
from collections import Counter, defaultdict
from typing import Any, Optional

from ..domains import DomainPack
from ..models import Entity, Event, Incident, TopoEdge
from ..store import Store

# ── events ───────────────────────────────────────────────────────────────────


def ingest_events(store: Store, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Generic event ingestion. Required per row: entity (or entity_id),
    type (or event_type), ts (epoch seconds or ISO-8601). Optional: severity
    (1..5), incident_id, plus any extra fields (kept in raw)."""
    events: list[Event] = []
    errors = 0
    known = {e.entity_id for e in store.entities()}
    new_entities: set[str] = set()
    for row in rows:
        try:
            entity = str(row.get("entity") or row.get("entity_id") or "").strip()
            etype = str(row.get("type") or row.get("event_type") or "").strip()
            ts = _parse_ts(row.get("ts") or row.get("timestamp"))
            if not entity or not etype or ts is None:
                errors += 1
                continue
            raw = {k: v for k, v in row.items()
                   if k not in ("entity", "entity_id", "type", "event_type",
                                "ts", "timestamp", "severity", "incident_id")}
            events.append(Event(
                incident_id=row.get("incident_id"), entity_id=entity,
                event_type=etype, severity=int(row.get("severity", 3)),
                ts=ts, raw=raw))
            if entity not in known:
                new_entities.add(entity)
        except Exception:
            errors += 1
    # auto-register unseen entities so nothing is silently dropped
    for ent in new_entities:
        store.put_entity(Entity(entity_id=ent, kind="ne",
                                layer=_infer_layer(ent), site=""))
        known.add(ent)
    if events:
        store.add_events(events)
    return {"ingested": len(events), "errors": errors,
            "new_entities": sorted(new_entities)}


def _parse_ts(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) / (1000.0 if v > 1e12 else 1.0)   # ms tolerated
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _infer_layer(entity: str) -> str:
    low = entity.lower()
    for token, layer in (("db", "data"), ("sql", "data"), ("cache", "data"),
                         ("queue", "data"), ("kafka", "data"), ("api", "app"),
                         ("svc", "app"), ("service", "app"), ("pod", "app"),
                         ("node", "infra"), ("host", "infra"), ("vm", "infra"),
                         ("router", "network"), ("switch", "network"),
                         ("lb", "network"), ("gw", "network")):
        if token in low:
            return layer
    return "general"


def ingest_alertmanager(store: Store, payload: dict[str, Any]) -> dict[str, Any]:
    """Prometheus Alertmanager webhook → events. Entity from instance/pod/
    service/job labels; type from alertname; severity from the severity label."""
    sev_map = {"critical": 1, "error": 2, "warning": 3, "info": 4}
    rows = []
    for alert in payload.get("alerts", []):
        labels = alert.get("labels", {})
        entity = (labels.get("instance") or labels.get("pod")
                  or labels.get("service") or labels.get("job") or "unknown")
        rows.append({
            "entity": entity,
            "type": labels.get("alertname", "alert"),
            "ts": alert.get("startsAt") or time.time(),
            "severity": sev_map.get(labels.get("severity", ""), 3),
            "labels": labels,
            "annotations": alert.get("annotations", {}),
            "source": "alertmanager",
        })
    return ingest_events(store, rows)


# ── topology ─────────────────────────────────────────────────────────────────

def ingest_topology(store: Store, payload: dict[str, Any]) -> dict[str, Any]:
    """entities: [{entity_id, kind?, layer?, site?, attrs?}]  ·  kind 'service'
    entities should carry attrs.paths (list of element-id lists) for blast/SLA
    analysis; kind 'power' marks shared infrastructure (latent-confounder class).
    edges: [{src, dst, relation}] with relation in carries|feeds|peers|serves."""
    now_epoch = float(payload.get("valid_from", 1_700_000_000))
    n_e = n_t = 0
    for row in payload.get("entities", []):
        if not row.get("entity_id"):
            continue
        store.put_entity(Entity(
            entity_id=row["entity_id"], kind=row.get("kind", "ne"),
            layer=row.get("layer", _infer_layer(row["entity_id"])),
            vendor=row.get("vendor", ""), model=row.get("model", ""),
            site=row.get("site", ""), attrs=row.get("attrs", {})))
        n_e += 1
    for row in payload.get("edges", []):
        if not (row.get("src") and row.get("dst")):
            continue
        store.put_topo_edge(TopoEdge(
            src=row["src"], dst=row["dst"],
            relation=row.get("relation", "carries"), valid_from=now_epoch))
        n_t += 1
    return {"entities": n_e, "edges": n_t}


def infer_topology(store: Store, window_s: float = 90.0,
                   min_lift: float = 3.0, min_count: int = 4) -> dict[str, Any]:
    """When no topology exists, infer adjacency from event co-occurrence lift.

    Entities whose events co-occur within `window_s` far more often than chance
    (PMI-style lift) get 'peers' edges. An honest prior, not ground truth —
    uploading real topology always beats inferring it, and the UI says so.
    """
    events = store.events_between(0, time.time() + 1)
    by_time = sorted((e.ts, e.entity_id) for e in events)
    occur: Counter = Counter(ent for _, ent in by_time)
    pair_count: Counter = Counter()
    for i, (ts_i, ent_i) in enumerate(by_time):
        j = i + 1
        while j < len(by_time) and by_time[j][0] - ts_i <= window_s:
            ent_j = by_time[j][1]
            if ent_j != ent_i:
                pair_count[tuple(sorted((ent_i, ent_j)))] += 1
            j += 1
    total = max(len(by_time), 1)
    added = 0
    for (a, b), c in pair_count.items():
        if c < min_count:
            continue
        expected = occur[a] * occur[b] / total
        if expected > 0 and c / expected >= min_lift:
            store.put_topo_edge(TopoEdge(src=a, dst=b, relation="peers",
                                         valid_from=1_700_000_000))
            added += 1
    return {"inferred_edges": added, "method": f"co-occurrence lift ≥ {min_lift}"}


# ── labeled history (the calibration seed) ───────────────────────────────────

def ingest_labeled_incidents(store: Store, rows: list[dict[str, Any]]
                             ) -> dict[str, Any]:
    """rows: [{t0, t1, root_cause_entity, root_cause_type, title?, incident_id?}].
    Events already ingested inside [t0, t1] are attached to the incident."""
    created = []
    for i, row in enumerate(rows):
        t0, t1 = _parse_ts(row.get("t0")), _parse_ts(row.get("t1"))
        if t0 is None or t1 is None:
            continue
        inc_id = row.get("incident_id") or f"INC-U{int(t0) % 10_000_000:07d}-{i}"
        # labels are authoritative: claim unassigned events AND events the
        # live detector already grouped (auto windows yield to postmortems)
        in_window = store.events_between(t0, t1)
        evs = [e for e in in_window
               if e.incident_id is None or str(e.incident_id).startswith("INC-D")]
        displaced = {e.incident_id for e in evs
                     if e.incident_id and str(e.incident_id).startswith("INC-D")}
        store.assign_incident([e.event_id for e in evs], inc_id)
        for d in displaced:
            if not store.events_for(d):
                store.delete_incident(d)
        truth = None
        if row.get("root_cause_entity") and row.get("root_cause_type"):
            truth = f"{row['root_cause_entity']}|{row['root_cause_type']}"
        store.put_incident(Incident(
            incident_id=inc_id,
            title=row.get("title", f"Labeled incident {inc_id}"),
            scenario="", severity=row.get("severity", "P1"), t0=t0, t1=t1,
            status="resolved", ground_truth=truth,
            entities=sorted({e.entity_id for e in evs}),
            alarm_count=len(evs), sla_services=[]))
        created.append(inc_id)
    return {"incidents": len(created), "ids": created[:20]}


# ── autonomous incident detection ────────────────────────────────────────────

def detect_incidents(store: Store, pack: DomainPack,
                     now: Optional[float] = None,
                     horizon_s: Optional[float] = 48 * 3600) -> list[Incident]:
    """Sessionize unassigned events into incident windows. A window CLOSES
    (and becomes an incident) once quiet for gap_seconds; open windows keep
    accumulating. Live watch only looks `horizon_s` back — bulk-loaded history
    is for learning and labeling, not live detection (pass None to window it
    all, as learn_workspace does)."""
    now = now or time.time()
    t_min = 0.0 if horizon_s is None else now - horizon_s
    unassigned = [e for e in store.events_between(t_min, now)
                  if e.incident_id is None]
    unassigned.sort(key=lambda e: e.ts)
    windows: list[list[Event]] = []
    cur: list[Event] = []
    for e in unassigned:
        if cur and e.ts - cur[-1].ts > pack.gap_seconds:
            windows.append(cur)
            cur = []
        cur.append(e)
    if cur:
        if now - cur[-1].ts > pack.gap_seconds:
            windows.append(cur)          # quiet long enough → closed
        # else: still burning; leave unassigned until it closes

    created: list[Incident] = []
    for w in windows:
        if len(w) < pack.min_events:
            continue
        inc_id = f"INC-D{int(w[0].ts) % 10_000_000:07d}"
        if store.incident(inc_id) is not None:
            inc_id = f"{inc_id}-{int(w[-1].ts) % 997}"
        store.assign_incident([e.event_id for e in w], inc_id)
        impacted = sorted({e.entity_id for e in w
                           if e.event_type in pack.outage_types
                           or e.event_type in pack.degradation_types})
        top_entity = Counter(e.entity_id for e in w).most_common(1)[0][0]
        inc = Incident(
            incident_id=inc_id,
            title=f"{'P1' if impacted else 'P2'}: detected burst around {top_entity} "
                  f"({len(w)} events)",
            scenario="", severity="P1" if impacted else "P2",
            t0=w[0].ts, t1=w[-1].ts + 60, status="open", ground_truth=None,
            entities=sorted({e.entity_id for e in w}),
            alarm_count=len(w), sla_services=impacted)
        store.put_incident(inc)
        created.append(inc)
    return created


# ── vocabulary suggestions ───────────────────────────────────────────────────

_IMPACT_WORDS = re.compile(
    r"outage|breach|down|lost|loss|unavail|stop|fail|sev1|p1|error.?rate|5xx|dead")
_DEGRADE_WORDS = re.compile(
    r"latenc|slow|degrad|brown|partial|drift|backlog|queue|saturat|high|warn")
_CHANGE_WORDS = re.compile(
    r"deploy|release|push|change|config|rollout|recipe|setting|update|migration")


def suggest_vocabulary(store: Store) -> dict[str, Any]:
    """Observed event types + keyword-based suggestions for the profile.
    The operator confirms; KEEL never silently decides what impact means."""
    counts: Counter = Counter()
    for e in store.events_between(0, time.time() + 1):
        counts[e.event_type] += 1
    types = [{"type": t, "count": c,
              "suggest": ("outage" if _IMPACT_WORDS.search(t.lower())
                          else "degradation" if _DEGRADE_WORDS.search(t.lower())
                          else "change" if _CHANGE_WORDS.search(t.lower())
                          else "")}
             for t, c in counts.most_common(200)]
    return {"types": types, "total_events": sum(counts.values())}
