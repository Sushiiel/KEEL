"""SQLite-backed persistence.

One file, zero external services — the reference deployment runs anywhere.
The interface is deliberately narrow so a production deployment can swap in
Postgres/TimescaleDB + a graph store without touching the planes above.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Iterable, Optional

from .config import DB_PATH
from .models import (Certificate, Entity, Event, Incident, Outcome, TopoEdge)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entity (
  entity_id TEXT PRIMARY KEY, kind TEXT, layer TEXT, vendor TEXT, model TEXT,
  site TEXT, attrs TEXT
);
CREATE TABLE IF NOT EXISTS alias (
  raw_name TEXT PRIMARY KEY, entity_id TEXT, method TEXT, confidence REAL
);
CREATE TABLE IF NOT EXISTS topo_edge (
  src TEXT, dst TEXT, relation TEXT, valid_from REAL, valid_to REAL,
  PRIMARY KEY (src, dst, relation, valid_from)
);
CREATE TABLE IF NOT EXISTS event (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  incident_id TEXT, entity_id TEXT, event_type TEXT, severity INTEGER,
  ts REAL, raw TEXT, suppressed INTEGER DEFAULT 0, info_gain REAL DEFAULT 1.0
);
CREATE INDEX IF NOT EXISTS ix_event_ts ON event (ts);
CREATE INDEX IF NOT EXISTS ix_event_inc ON event (incident_id);
CREATE TABLE IF NOT EXISTS incident (
  incident_id TEXT PRIMARY KEY, title TEXT, scenario TEXT, severity TEXT,
  t0 REAL, t1 REAL, status TEXT, ground_truth TEXT, entities TEXT,
  alarm_count INTEGER, sla_services TEXT
);
CREATE TABLE IF NOT EXISTS causal_edge (
  graph_version TEXT, src_type TEXT, dst_type TEXT,
  lag_lo_ms INTEGER, lag_hi_ms INTEGER, strength REAL, method TEXT,
  stability REAL, provenance TEXT, pinned_by TEXT, pinned_reason TEXT,
  PRIMARY KEY (graph_version, src_type, dst_type)
);
CREATE TABLE IF NOT EXISTS certificate (
  cert_id TEXT PRIMARY KEY, incident_id TEXT, created_at REAL, payload TEXT
);
CREATE TABLE IF NOT EXISTS outcome (
  cert_id TEXT PRIMARY KEY, payload TEXT
);
CREATE TABLE IF NOT EXISTS translog (
  idx INTEGER PRIMARY KEY, leaf_hash TEXT, cert_id TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS kv (
  k TEXT PRIMARY KEY, v TEXT
);
"""


class Store:
    def __init__(self, path: str | None = None):
        self._db = sqlite3.connect(str(path or DB_PATH), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._db.commit()

    # ── generic helpers ──────────────────────────────────────────────────────
    def _exec(self, sql: str, args: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._db.execute(sql, tuple(args))
            self._db.commit()
            return cur

    def _rows(self, sql: str, args: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, tuple(args)).fetchall()

    def kv_get(self, k: str, default: Any = None) -> Any:
        rows = self._rows("SELECT v FROM kv WHERE k=?", (k,))
        return json.loads(rows[0]["v"]) if rows else default

    def kv_set(self, k: str, v: Any) -> None:
        self._exec("INSERT OR REPLACE INTO kv (k,v) VALUES (?,?)", (k, json.dumps(v)))

    # ── entities & topology ──────────────────────────────────────────────────
    def put_entity(self, e: Entity) -> None:
        self._exec(
            "INSERT OR REPLACE INTO entity VALUES (?,?,?,?,?,?,?)",
            (e.entity_id, e.kind, e.layer, e.vendor, e.model, e.site, json.dumps(e.attrs)),
        )

    def entities(self) -> list[Entity]:
        return [
            Entity(entity_id=r["entity_id"], kind=r["kind"], layer=r["layer"],
                   vendor=r["vendor"], model=r["model"], site=r["site"],
                   attrs=json.loads(r["attrs"] or "{}"))
            for r in self._rows("SELECT * FROM entity")
        ]

    def put_alias(self, raw: str, entity_id: str, method: str, confidence: float) -> None:
        self._exec("INSERT OR REPLACE INTO alias VALUES (?,?,?,?)",
                   (raw, entity_id, method, confidence))

    def aliases(self) -> dict[str, tuple[str, str, float]]:
        return {r["raw_name"]: (r["entity_id"], r["method"], r["confidence"])
                for r in self._rows("SELECT * FROM alias")}

    def put_topo_edge(self, e: TopoEdge) -> None:
        self._exec("INSERT OR REPLACE INTO topo_edge VALUES (?,?,?,?,?)",
                   (e.src, e.dst, e.relation, e.valid_from, e.valid_to))

    def topology_at(self, t: float) -> list[TopoEdge]:
        """Bi-temporal query: edges valid at time t."""
        rows = self._rows(
            "SELECT * FROM topo_edge WHERE valid_from<=? AND (valid_to IS NULL OR valid_to>?)",
            (t, t))
        return [TopoEdge(src=r["src"], dst=r["dst"], relation=r["relation"],
                         valid_from=r["valid_from"], valid_to=r["valid_to"]) for r in rows]

    # ── events & incidents ───────────────────────────────────────────────────
    def add_events(self, events: list[Event]) -> None:
        with self._lock:
            self._db.executemany(
                "INSERT INTO event (incident_id,entity_id,event_type,severity,ts,raw,suppressed,info_gain)"
                " VALUES (?,?,?,?,?,?,?,?)",
                [(ev.incident_id, ev.entity_id, ev.event_type, ev.severity, ev.ts,
                  json.dumps(ev.raw), int(ev.suppressed), ev.info_gain) for ev in events])
            self._db.commit()

    def events_for(self, incident_id: str) -> list[Event]:
        return [self._event(r) for r in self._rows(
            "SELECT * FROM event WHERE incident_id=? ORDER BY ts", (incident_id,))]

    def assign_incident(self, event_ids: list[int], incident_id: str) -> None:
        with self._lock:
            self._db.executemany(
                "UPDATE event SET incident_id=? WHERE event_id=?",
                [(incident_id, eid) for eid in event_ids])
            self._db.commit()

    def events_between(self, t0: float, t1: float) -> list[Event]:
        return [self._event(r) for r in self._rows(
            "SELECT * FROM event WHERE ts>=? AND ts<=? ORDER BY ts", (t0, t1))]

    @staticmethod
    def _event(r: sqlite3.Row) -> Event:
        return Event(event_id=r["event_id"], incident_id=r["incident_id"],
                     entity_id=r["entity_id"], event_type=r["event_type"],
                     severity=r["severity"], ts=r["ts"], raw=json.loads(r["raw"] or "{}"),
                     suppressed=bool(r["suppressed"]), info_gain=r["info_gain"])

    def put_incident(self, i: Incident) -> None:
        self._exec(
            "INSERT OR REPLACE INTO incident VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (i.incident_id, i.title, i.scenario, i.severity, i.t0, i.t1, i.status,
             i.ground_truth, json.dumps(i.entities), i.alarm_count,
             json.dumps(i.sla_services)))

    def incident(self, incident_id: str) -> Optional[Incident]:
        rows = self._rows("SELECT * FROM incident WHERE incident_id=?", (incident_id,))
        return self._incident(rows[0]) if rows else None

    def delete_incident(self, incident_id: str) -> None:
        self._exec("DELETE FROM incident WHERE incident_id=?", (incident_id,))

    def incidents(self, limit: int = 200) -> list[Incident]:
        return [self._incident(r) for r in self._rows(
            "SELECT * FROM incident ORDER BY t0 DESC LIMIT ?", (limit,))]

    @staticmethod
    def _incident(r: sqlite3.Row) -> Incident:
        return Incident(incident_id=r["incident_id"], title=r["title"],
                        scenario=r["scenario"], severity=r["severity"], t0=r["t0"],
                        t1=r["t1"], status=r["status"], ground_truth=r["ground_truth"],
                        entities=json.loads(r["entities"] or "[]"),
                        alarm_count=r["alarm_count"],
                        sla_services=json.loads(r["sla_services"] or "[]"))

    # ── causal graph, versioned ──────────────────────────────────────────────
    def put_causal_edges(self, version: str, edges: list[dict[str, Any]]) -> None:
        with self._lock:
            self._db.execute("DELETE FROM causal_edge WHERE graph_version=?", (version,))
            self._db.executemany(
                "INSERT OR REPLACE INTO causal_edge VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [(version, e["src_type"], e["dst_type"], e.get("lag_lo_ms", 0),
                  e.get("lag_hi_ms", 0), e.get("strength", 0.0), e.get("method", ""),
                  e.get("stability", 0.0), e.get("provenance", "learned"),
                  e.get("pinned_by", ""), e.get("pinned_reason", "")) for e in edges])
            self._db.commit()

    def causal_edges(self, version: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self._rows(
            "SELECT * FROM causal_edge WHERE graph_version=?", (version,))]

    # ── certificates, outcomes, transparency log ─────────────────────────────
    def put_certificate(self, c: Certificate) -> None:
        self._exec("INSERT OR REPLACE INTO certificate VALUES (?,?,?,?)",
                   (c.cert_id, c.incident_id, c.created_at, c.model_dump_json()))

    def certificate(self, cert_id: str) -> Optional[Certificate]:
        rows = self._rows("SELECT payload FROM certificate WHERE cert_id=?", (cert_id,))
        return Certificate.model_validate_json(rows[0]["payload"]) if rows else None

    def certificates(self, limit: int = 200) -> list[Certificate]:
        return [Certificate.model_validate_json(r["payload"]) for r in self._rows(
            "SELECT payload FROM certificate ORDER BY created_at DESC LIMIT ?", (limit,))]

    def certificates_for_incident(self, incident_id: str) -> list[Certificate]:
        return [Certificate.model_validate_json(r["payload"]) for r in self._rows(
            "SELECT payload FROM certificate WHERE incident_id=? ORDER BY created_at",
            (incident_id,))]

    def put_outcome(self, o: Outcome) -> None:
        self._exec("INSERT OR REPLACE INTO outcome VALUES (?,?)",
                   (o.cert_id, o.model_dump_json()))

    def outcomes(self) -> list[Outcome]:
        return [Outcome.model_validate_json(r["payload"])
                for r in self._rows("SELECT payload FROM outcome")]

    def append_translog(self, idx: int, leaf_hash: str, cert_id: str, ts: float) -> None:
        self._exec("INSERT INTO translog VALUES (?,?,?,?)", (idx, leaf_hash, cert_id, ts))

    def translog(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._rows("SELECT * FROM translog ORDER BY idx")]


_stores: dict[str, Store] = {}


def get_store(domain: str = "telecom") -> Store:
    """One SQLite store per domain workspace."""
    if domain not in _stores:
        from .config import DATA_DIR
        _stores[domain] = Store(path=DATA_DIR / f"keel-{domain}.db")
    return _stores[domain]
