"""The drift gate: detect exchangeability violations and act on them.

Three monitors, in escalating order of consequence:
  energy distance     multivariate two-sample distance between recent incident
                      features and the calibration corpus features
  structural drift    normalized graph edit distance between successive causal
                      graph versions
  fidelity residual   rolling |predicted - observed| over certified actions

Breach policy: widen conformal intervals -> abstain -> page a human.
Abstention is a first-class output; silent certifying under shift is the one
failure mode this product must never have.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from ..config import (DRIFT_ENERGY_BREACH, DRIFT_ENERGY_WARN, DRIFT_GED_BREACH)
from ..models import DriftReport
from ..store import Store
from ..structure.ensemble import graph_edit_distance_norm

FEATURES_KEY = "incident_features"     # rolling list of feature vectors
FIDELITY_KEY = "fidelity_ledger"       # list of {action_class, err, ts}


def incident_features(alarm_count: int, n_layers: int, duration_s: float,
                      compression: float, n_entities: int) -> list[float]:
    return [
        float(np.log1p(alarm_count)),
        float(n_layers),
        float(np.log1p(max(duration_s, 1)) / 3.0),
        float(compression),
        float(np.log1p(n_entities)),
    ]


def record_features(store: Store, feats: list[float], baseline: bool) -> None:
    key = FEATURES_KEY + ("_base" if baseline else "_recent")
    lst = store.kv_get(key, [])
    lst.append([round(f, 4) for f in feats])
    store.kv_set(key, lst[-300:])


def energy_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Szekely-Rizzo energy distance between two multivariate samples."""
    if len(a) < 4 or len(b) < 4:
        return 0.0
    def _mean_pdist(x: np.ndarray, y: np.ndarray) -> float:
        d = np.linalg.norm(x[:, None, :] - y[None, :, :], axis=2)
        return float(d.mean())
    ab = _mean_pdist(a, b)
    aa = _mean_pdist(a, a)
    bb = _mean_pdist(b, b)
    scale = max(aa + bb, 1e-9)
    return max(0.0, (2 * ab - aa - bb) / scale)


def fidelity_residual(store: Store, action_class: str | None = None) -> float:
    ledger = store.kv_get(FIDELITY_KEY, [])
    if action_class:
        ledger = [x for x in ledger if x["action_class"] == action_class]
    # below 5 observations there is no residual ESTIMATE, only anecdotes;
    # the gate must not breach on a single unlucky draw
    if len(ledger) < 5:
        return 0.0
    recent = ledger[-30:]
    return float(np.mean([x["err"] for x in recent]))


def record_fidelity(store: Store, action_class: str, err: float,
                    ts: float) -> None:
    ledger = store.kv_get(FIDELITY_KEY, [])
    ledger.append({"action_class": action_class, "err": round(float(err), 4),
                   "ts": ts})
    store.kv_set(FIDELITY_KEY, ledger[-500:])


def check_drift(store: Store) -> DriftReport:
    base = np.array(store.kv_get(FEATURES_KEY + "_base", []) or [[0] * 5])
    recent_list = store.kv_get(FEATURES_KEY + "_recent", [])
    recent = np.array(recent_list[-40:] or [[0] * 5])
    ed = energy_distance(base, recent) if len(recent_list) >= 4 else 0.0
    ged = graph_edit_distance_norm(store)
    fid = fidelity_residual(store)

    notes: list[str] = []
    level = "ok"
    if ed > DRIFT_ENERGY_WARN:
        level = "widened"
        notes.append(f"feature drift {ed:.3f} > warn {DRIFT_ENERGY_WARN} — intervals widened")
    if ed > DRIFT_ENERGY_BREACH:
        level = "breach"
        notes.append(f"feature drift {ed:.3f} > breach {DRIFT_ENERGY_BREACH} — abstaining")
    if ged > DRIFT_GED_BREACH:
        level = "breach"
        notes.append(f"causal graph moved {ged:.0%} between versions — recalibration required")
    if fid > 0.5:
        level = "breach"
        notes.append(f"twin fidelity residual {fid:.2f} — refusing to certify actions")
    if not notes:
        notes.append("exchangeability monitors nominal")
    return DriftReport(energy_distance=round(ed, 4),
                       graph_edit_distance=round(ged, 4),
                       fidelity_residual=round(fid, 4),
                       level=level, notes=notes)


def widen_amount(report: DriftReport) -> float:
    if report.level == "widened":
        return 0.10
    return 0.0
