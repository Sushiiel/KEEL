"""Generic incident simulator: physically-plausible cascades over any domain.

The domain pack owns the *true* generative structure (rules, delays,
probabilities) and the world topology; this engine propagates failures over
them. The rest of KEEL never sees the rules — discovery, adjudication, and
calibration must recover structure from the event stream alone, which is what
keeps the demo honest in every industry.

Selectors (relations are the canonical schema, so these are domain-free):
  same             the same entity
  same_kind:<pfx>  the same entity, only if its id starts with <pfx>
  fed              entities this shared-infra element feeds (non-link)
  carried          entities this element carries traffic/flow to
  peers            lateral neighbors
  services         SVC: entities mapped over this element
"""
from __future__ import annotations

import heapq
from typing import Optional

import numpy as np

from ..domains import DomainPack, Scenario
from ..models import Event, Incident
from ..store import Store
from .world import service_paths

DAY = 86_400.0


def variable(entity_id: str, event_type: str) -> str:
    return f"{entity_id}|{event_type}"


class Cascade:
    def __init__(self, store: Store, pack: DomainPack, t0: float,
                 rng: np.random.Generator, root_entity: str):
        self.store, self.pack, self.rng = store, pack, rng
        self.t0, self.root_entity = t0, root_entity
        self.topo = store.topology_at(t0)
        self.paths = service_paths(store)
        self.fired: set[str] = set()
        self.instances: list[tuple[float, str, str]] = []
        self.failed_elements: set[str] = set()

    def _out(self, ent: str, relation: str) -> list[str]:
        return [e.dst for e in self.topo if e.src == ent and e.relation == relation]

    def targets(self, selector: str, entity: str) -> list[str]:
        if selector == "same":
            return [entity]
        if selector.startswith("same_kind:"):
            return [entity] if entity.startswith(selector.split(":", 1)[1]) else []
        if selector == "fed":
            return [d for d in self._out(entity, "feeds") if not d.startswith("SVC:")]
        if selector == "carried":
            return self._out(entity, "carries")
        if selector == "peers":
            return self._out(entity, "peers")
        if selector == "services":
            return self._out(entity, "serves")
        return []

    def _path_alive(self, path: list[str]) -> bool:
        return not any(el in self.failed_elements for el in path)

    def run(self, root_type: str, config_error: bool = False,
            horizon_s: float = 900.0) -> list[tuple[float, str, str]]:
        q: list[tuple[float, str, str]] = [(self.t0, self.root_entity, root_type)]
        hard = self.pack.hard_down_types | {"mpls.lsp_down"}
        while q:
            ts, entity, etype = heapq.heappop(q)
            if ts - self.t0 > horizon_s:
                continue
            key = variable(entity, etype)
            if key in self.fired:
                continue
            self.fired.add(key)
            self.instances.append((ts, entity, etype))
            if etype in hard:
                self.failed_elements.add(entity)

            for src, dst, sel, prob, lo, hi in self.pack.true_rules:
                if src != etype:
                    continue
                p = 0.92 if (src == "cfg.push" and config_error) else prob
                for tgt in self.targets(sel, entity):
                    if dst == "svc.impact":
                        plist = self.paths.get(tgt) or [[], []]
                        prim = plist[0] if plist else []
                        bak = plist[1] if len(plist) > 1 else []
                        etype_out = (self.pack.impact_protected_type
                                     if bak and self._path_alive(bak) and entity in prim
                                     else self.pack.impact_outage_type)
                    else:
                        etype_out = dst
                    if self.rng.random() < p:
                        delay = float(self.rng.uniform(lo, hi))
                        heapq.heappush(q, (ts + delay, tgt, etype_out))
        return self.instances


def simulate_incident(store: Store, pack: DomainPack, scenario_key: str,
                      t0: float, rng: np.random.Generator, incident_id: str,
                      root_entity: Optional[str] = None, status: str = "open",
                      ) -> tuple[Incident, list[Event]]:
    if scenario_key == "novel_storm":
        return _simulate_novel_storm(store, pack, t0, rng, incident_id, status)
    sc = pack.scenarios[scenario_key]
    root = root_entity or sc.pick_entity(rng)
    cascade = Cascade(store, pack, t0, rng, root)
    instances = cascade.run(sc.root_type, config_error=sc.config_error)

    events: list[Event] = []
    for ts, entity, etype in instances:
        if sc.hidden_root and entity == root and etype == sc.root_type:
            continue                    # the root cause is invisible to monitoring
        n_dup = int(rng.integers(*sc.dup))
        names = pack.raw_namer(entity)
        for d in range(max(1, n_dup)):
            raw_name = names[int(rng.integers(0, len(names)))]
            events.append(Event(
                incident_id=incident_id, entity_id=entity, event_type=etype,
                severity=pack.severity.get(etype, 3),
                ts=ts + float(rng.uniform(0, 2.5)) * d,
                raw={"source": ["nms", "syslog", "snmp", "telemetry"][d % 4],
                     "raw_name": raw_name,
                     "layer": pack.layer_of_type.get(etype, "ip"),
                     "text": f"{etype.replace('.', ' ').upper()} reported on {raw_name}"}))

    t_end = max((e.ts for e in events), default=t0) + 60
    n_noise = int(rng.integers(*sc.noise))
    all_entities = [e.entity_id for e in store.entities() if e.kind in ("ne", "link")]
    for _ in range(n_noise):
        ent = str(rng.choice(all_entities))
        nt = str(rng.choice(pack.noise_types))
        events.append(Event(
            incident_id=incident_id, entity_id=ent, event_type=nt, severity=4,
            ts=float(rng.uniform(t0, t_end)),
            raw={"source": "nms", "raw_name": pack.raw_namer(ent)[0], "layer": "env",
                 "text": f"{nt} on {ent}"}))

    events.sort(key=lambda e: e.ts)
    impacted = sorted({e.entity_id for e in events
                       if e.event_type in pack.outage_types})
    incident = Incident(
        incident_id=incident_id, title=sc.title(root), scenario=scenario_key,
        severity=sc.severity, t0=t0, t1=t_end, status=status,
        ground_truth=variable(root, sc.root_type),
        entities=sorted({e.entity_id for e in events}),
        alarm_count=len(events), sla_services=impacted)
    return incident, events


def _simulate_novel_storm(store: Store, pack: DomainPack, t0: float,
                          rng: np.random.Generator, incident_id: str, status: str
                          ) -> tuple[Incident, list[Event]]:
    """A failure mode the calibration corpus has never seen — proves the drift
    gate abstains instead of confidently certifying nonsense."""
    all_entities = [e.entity_id for e in store.entities()
                    if e.kind in ("ne", "link")]
    types = list(pack.severity) + pack.noise_types
    events: list[Event] = []
    for _ in range(int(rng.integers(180, 260))):
        ent = str(rng.choice(all_entities))
        et = str(rng.choice(types))
        events.append(Event(
            incident_id=incident_id, entity_id=ent, event_type=et,
            severity=int(rng.integers(1, 5)), ts=t0 + float(rng.uniform(0, 2400)),
            raw={"source": "nms", "raw_name": pack.raw_namer(ent)[0],
                 "layer": pack.layer_of_type.get(et, "env"), "text": f"{et} on {ent}"}))
    events.sort(key=lambda e: e.ts)
    incident = Incident(
        incident_id=incident_id, title="P1: Uncharacterized multi-layer alarm storm",
        scenario="novel_storm", severity="P1", t0=t0, t1=events[-1].ts + 60,
        status=status, ground_truth=None,
        entities=sorted({e.entity_id for e in events}),
        alarm_count=len(events), sla_services=[])
    return incident, events


# ── History seeding ──────────────────────────────────────────────────────────

def seed_history(store: Store, pack: DomainPack) -> list[Incident]:
    rng = np.random.default_rng(pack.seed)
    incidents: list[Incident] = []
    start = 1_754_000_000.0 + 2 * DAY
    span = pack.history_days * DAY - 4 * DAY
    times = np.sort(rng.uniform(start, start + span, size=pack.history_incidents))
    keys = list(pack.scenario_weights)
    probs = np.array([pack.scenario_weights[k] for k in keys])
    probs = probs / probs.sum()

    for i, t0 in enumerate(times):
        key = str(rng.choice(keys, p=probs))
        inc, events = simulate_incident(store, pack, key, float(t0), rng,
                                        f"INC-H{i:04d}", status="resolved")
        store.put_incident(inc)
        store.add_events(events)
        incidents.append(inc)

    # benign standalone change events — natural experiments for discovery
    ne_entities = [e.entity_id for e in store.entities() if e.kind == "ne"]
    for _ in range(60):
        t = float(rng.uniform(start, start + span))
        ent = str(rng.choice(ne_entities))
        store.add_events([Event(
            incident_id=None, entity_id=ent, event_type="cfg.push", severity=4, ts=t,
            raw={"source": "cicd", "raw_name": pack.raw_namer(ent)[0],
                 "text": f"routine change on {ent}", "layer": "ip"})])
    return incidents


def seed_canonical_incident(store: Store, pack: DomainPack, now: float) -> Incident:
    c = pack.canonical
    rng = np.random.default_rng(c["seed"])
    inc, events = simulate_incident(
        store, pack, c["scenario"], now - 11 * 60, rng, c["incident_id"],
        root_entity=c["root_entity"], status="open")
    inc.title = c["title"]
    store.put_incident(inc)
    store.add_events(events)
    return inc
