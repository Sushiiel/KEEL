"""Adaptive statistics: drift-aware calibration + behavioral anomaly detection.

Two upgrades the research demanded before the guarantees can be trusted in
the wild:

1. **Rolling, drift-audited calibration.** The Clopper-Pearson bound is only
   honest under exchangeability. A Page-Hinkley change detector watches each
   (agent, action-class) outcome stream; on detected regime change the bound
   is computed over the post-change window only and the certificate carries a
   drift flag — bounds are never silently averaged across a behavior change.

2. **Behavioral anomaly scoring.** A per-agent first-order Markov profile of
   action-class transitions (Laplace-smoothed, learned online from decided
   requests). A transition whose smoothed probability is far below the
   agent's own baseline raises an advisory anomaly — the signature of a
   hijacked or manipulated agent suddenly doing unusual things. Advisory
   only: it can escalate, never approve (the monotone rule).
"""
from __future__ import annotations

import math
from typing import Any, Optional

from ..store import Store

_PH_DELTA = 0.05        # Page-Hinkley insensitivity
_PH_LAMBDA = 1.5        # detection threshold
_ROLL_W = 100           # rolling calibration window
_ANOM_KEY = "gw_behavior"


def page_hinkley_change(outcomes: list[int]) -> Optional[int]:
    """Return index of detected change in a 0/1 outcome stream, else None.
    Two-sided Page-Hinkley on the success indicator."""
    if len(outcomes) < 10:
        return None
    # proper two-sided PH: separate delta-shifted accumulators per direction,
    # so a steady stream never self-triggers
    mean = 0.0
    mt_inc = min_inc = 0.0        # detects upward shifts
    mt_dec = max_dec = 0.0        # detects downward shifts
    change_at = None
    for i, x in enumerate(outcomes):
        mean += (x - mean) / (i + 1)
        mt_inc += x - mean - _PH_DELTA
        min_inc = min(min_inc, mt_inc)
        mt_dec += x - mean + _PH_DELTA
        max_dec = max(max_dec, mt_dec)
        if mt_inc - min_inc > _PH_LAMBDA or max_dec - mt_dec > _PH_LAMBDA:
            return i          # first alarm = the change point the window needs
    return change_at


def adaptive_window(last: list[dict[str, Any]]) -> tuple[list[dict], bool]:
    """The outcome window the bound may honestly be computed over.
    Returns (window, drift_detected)."""
    recent = last[-_ROLL_W:]
    stream = [int(bool(o.get("success"))) for o in recent]
    cp = page_hinkley_change(stream)
    if cp is not None and cp < len(recent) - 3:
        return recent[cp + 1:], True
    return recent, False


# ── behavioral profile ───────────────────────────────────────────────────────

def observe_transition(store: Store, agent_id: str, action_class: str) -> None:
    prof = store.kv_get(_ANOM_KEY, {})
    ap = prof.get(agent_id, {"last": None, "trans": {}, "counts": {}, "n": 0})
    prev = ap.get("last") or "__start__"
    key = f"{prev}→{action_class}"
    ap["trans"][key] = int(ap["trans"].get(key, 0)) + 1
    ap["counts"][action_class] = int(ap["counts"].get(action_class, 0)) + 1
    ap["n"] = int(ap["n"]) + 1
    ap["last"] = action_class
    prof[agent_id] = ap
    store.kv_set(_ANOM_KEY, prof)


def anomaly_score(store: Store, agent_id: str, action_class: str
                  ) -> tuple[float, str]:
    """Surprisal (bits) of this transition under the agent's own profile,
    relative to its baseline entropy. High = unusual for THIS agent."""
    prof = store.kv_get(_ANOM_KEY, {}).get(agent_id)
    if not prof or int(prof.get("n", 0)) < 20:
        return 0.0, "profile still forming (n<20) — anomaly scoring inactive"
    prev = prof.get("last") or "__start__"
    trans = prof.get("trans", {})
    from_prev = {k: v for k, v in trans.items() if k.startswith(f"{prev}→")}
    total = sum(from_prev.values())
    vocab = max(len(prof.get("counts", {})), 1) + 1
    c = from_prev.get(f"{prev}→{action_class}", 0)
    p = (c + 1) / (total + vocab)                       # Laplace smoothing
    surprisal = -math.log2(p)
    # baseline: expected surprisal of the agent's own transition distribution
    if total > 0:
        probs = [(v + 1) / (total + vocab) for v in from_prev.values()]
        baseline = -sum(q * math.log2(q) for q in probs)
    else:
        baseline = math.log2(vocab)
    excess = max(0.0, surprisal - baseline)
    detail = (f"transition {prev}→{action_class}: surprisal {surprisal:.1f} bits "
              f"vs baseline {baseline:.1f} (excess {excess:.1f})")
    return excess, detail
