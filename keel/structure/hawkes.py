"""Alarm-burst modeling as a marked self-exciting point process.

We estimate a pairwise exponential excitation kernel between event types from
the historical corpus, then score every incoming alarm by how much of its
conditional intensity is explained by upstream events:

    lambda_k(t) = mu_k + sum_j a_jk * beta_jk * exp(-beta_jk (t - t_j))

An alarm whose arrival is mostly explained (excitation >> background) carries
little new information and is suppressed; its `info_gain` is 1 - explained
fraction. This replaces heuristic time-window suppression with a principled,
data-derived criterion.

Assumptions: within-incident stationarity of the kernel; exponential decay.
Breaks when: a genuinely novel cascade reuses familiar event types — the drift
gate (calibrate.drift) exists to catch exactly that.
"""
from __future__ import annotations

import math
from collections import defaultdict

import numpy as np

from ..models import Event

WINDOW_S = 300.0          # max lag considered as excitation
EXPLAIN_THRESHOLD = 0.75  # explained fraction above which alarm is suppressed
DUP_WINDOW_S = 30.0       # same (entity,type) within this window = duplicate


class HawkesModel:
    def __init__(self) -> None:
        self.mu: dict[str, float] = {}
        self.alpha: dict[tuple[str, str], float] = {}
        self.beta: dict[tuple[str, str], float] = {}
        self.fitted = False

    # ── estimation ───────────────────────────────────────────────────────────
    def fit(self, incidents_events: list[list[Event]]) -> "HawkesModel":
        """Method-of-moments estimation over per-incident sequences."""
        count_j: dict[str, int] = defaultdict(int)
        count_jk: dict[tuple[str, str], int] = defaultdict(int)
        lags: dict[tuple[str, str], list[float]] = defaultdict(list)
        unexplained: dict[str, int] = defaultdict(int)
        exposure_s: float = 0.0

        for evs in incidents_events:
            firsts: dict[tuple[str, str], float] = {}
            for e in sorted(evs, key=lambda x: x.ts):
                k = (e.entity_id, e.event_type)
                if k not in firsts:
                    firsts[k] = e.ts
            seq = sorted(((ts, et) for (_, et), ts in
                          [(k, v) for k, v in firsts.items()]), key=lambda x: x[0])
            if not seq:
                continue
            exposure_s += max(60.0, seq[-1][0] - seq[0][0])
            for i, (ts_i, t_i) in enumerate(seq):
                count_j[t_i] += 1
                excited = False
                for ts_p, t_p in seq[:i]:
                    dt = ts_i - ts_p
                    if 0 < dt <= WINDOW_S and t_p != t_i:
                        count_jk[(t_p, t_i)] += 1
                        lags[(t_p, t_i)].append(dt)
                        excited = True
                if not excited:
                    unexplained[t_i] += 1

        for j, k in count_jk:
            self.alpha[(j, k)] = min(1.5, count_jk[(j, k)] / max(count_j[j], 1))
            med = float(np.median(lags[(j, k)])) if lags[(j, k)] else 30.0
            self.beta[(j, k)] = 1.0 / max(med, 1.0)
        for t, n in count_j.items():
            self.mu[t] = max(unexplained[t], 1) / max(exposure_s, 60.0)
        self.fitted = True
        return self

    # ── scoring / suppression ────────────────────────────────────────────────
    def annotate(self, events: list[Event], adjacency=None) -> list[Event]:
        """Mark duplicates and excitation-explained alarms; set info_gain.

        When an adjacency index is provided, excitation only flows between
        topology-linked entities — an alarm is never 'explained' by an
        unrelated element's noise, which is what makes the suppression
        trustworthy enough to act on.
        """
        events = sorted(events, key=lambda e: e.ts)
        seen_first: dict[tuple[str, str], float] = {}
        history: list[tuple[float, str, str]] = []  # (ts, entity, type)

        for e in events:
            key = (e.entity_id, e.event_type)
            if key in seen_first and e.ts - seen_first[key] <= DUP_WINDOW_S:
                e.suppressed, e.info_gain = True, 0.02
                continue
            seen_first[key] = e.ts

            lam_exc = 0.0
            for ts_p, ent_p, t_p in history[-200:]:
                dt = e.ts - ts_p
                if not (0 < dt <= WINDOW_S):
                    continue
                if adjacency is not None and not adjacency.linked(ent_p, e.entity_id):
                    continue
                a = self.alpha.get((t_p, e.event_type), 0.0)
                b = self.beta.get((t_p, e.event_type), 1 / 30)
                lam_exc += a * b * math.exp(-b * dt)
            mu = self.mu.get(e.event_type, 1e-4)
            explained = lam_exc / (lam_exc + mu) if (lam_exc + mu) > 0 else 0.0
            e.info_gain = round(1.0 - explained, 4)
            e.suppressed = explained > EXPLAIN_THRESHOLD
            history.append((e.ts, e.entity_id, e.event_type))
        return events

    def intensity_trace(self, events: list[Event], etype: str,
                        n: int = 120) -> list[dict]:
        """lambda_k(t) sampled over the incident window — for the UI."""
        if not events:
            return []
        t0, t1 = events[0].ts, events[-1].ts + 30
        firsts: dict[tuple[str, str], float] = {}
        seq = []
        for e in sorted(events, key=lambda x: x.ts):
            k = (e.entity_id, e.event_type)
            if k not in firsts:
                firsts[k] = e.ts
                seq.append((e.ts, e.event_type))
        mu = self.mu.get(etype, 1e-4)
        out = []
        for t in np.linspace(t0, t1, n):
            lam = mu
            for ts_p, t_p in seq:
                dt = t - ts_p
                if 0 < dt <= WINDOW_S:
                    a = self.alpha.get((t_p, etype), 0.0)
                    b = self.beta.get((t_p, etype), 1 / 30)
                    lam += a * b * math.exp(-b * dt)
            out.append({"t": float(t), "lambda": float(lam)})
        return out
