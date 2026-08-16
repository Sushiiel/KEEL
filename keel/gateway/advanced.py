"""Frontier statistics for the gateway: anytime-valid bounds + risk control.

1. **Anytime-valid lower confidence bound** (Waudby-Smith & Ramdas betting
   martingale). Classical intervals (incl. Clopper-Pearson) are valid at ONE
   preplanned sample size — but a gateway checks the bound after EVERY
   outcome, which is continuous peeking and voids fixed-n guarantees. A
   betting confidence sequence is valid at every stopping time simultaneously
   (Ville's inequality): the bound can be consulted forever, after any n,
   and the coverage guarantee still holds. This is the mathematically correct
   object for streaming trust decisions.

   Construction: for candidate mean m, wealth K_t(m) = Π_t (1 + λ_t(x_t − m))
   with predictable bets λ_t clipped to keep wealth nonnegative; the lower
   bound is the smallest m not rejected, i.e. inf{m : K_t(m) < 1/α}.

2. **Conformal risk control on the ALLOW policy** (Angelopoulos et al.,
   generalized coverage → bounded expected loss). We treat "an allowed action
   caused harm" as the loss and maintain, per risk class, an exact binomial
   upper confidence bound on the harm rate among allowed-and-executed
   actions. If that UCB exceeds the class's harm budget, the gateway
   TIGHTENS itself: the required success floor for auto-allow rises and
   traffic shifts to escalation until the bound recovers. The knob is chosen
   by data, with a finite-sample guarantee — not by vibes.
"""
from __future__ import annotations

import math
from typing import Any, Optional

from ..store import Store

# harm-rate budgets per risk class for the ALLOW policy (CRC targets)
HARM_BUDGET = {"low": 0.05, "medium": 0.02, "high": 0.01, "critical": 0.005}
_CRC_KEY = "gw_crc"


def wsr_lower_bound(outcomes: list[int], alpha: float = 0.10,
                    grid: int = 200) -> float:
    """Anytime-valid lower confidence bound for a Bernoulli mean via the
    hedged betting martingale. Valid simultaneously over all t (Ville).

    Assumptions: outcomes bounded in [0,1] (binary here), conditionally
    exchangeable given the past. Breaks (like everything) under adversarial
    drift — which the Page-Hinkley window guard handles upstream.
    """
    n = len(outcomes)
    if n == 0:
        return 0.0
    threshold = 1.0 / alpha
    lo = 0.0
    # search the smallest non-rejected m from below
    for gi in range(grid + 1):
        m = gi / grid
        if m >= 1.0:
            return 1.0 - 1.0 / grid
        wealth = 1.0
        mean_hat, var_hat = 0.5, 0.25
        rejected = False
        for t, x in enumerate(outcomes, start=1):
            # predictable bet: scaled by running variance estimate, clipped so
            # wealth stays positive for any x in [0,1] given candidate m
            lam = math.sqrt(2 * math.log(2 / alpha) /
                            (max(var_hat, 1e-3) * t * math.log(t + 1) + 1e-9))
            lam = min(lam, 0.75 / max(m, 1e-3))          # keep 1+λ(x−m) > 0
            wealth *= max(1e-12, 1.0 + lam * (x - m))
            if wealth >= threshold:
                rejected = True
                break
            # update running moments (predictably, after betting)
            prev = mean_hat
            mean_hat += (x - mean_hat) / (t + 1)
            var_hat += ((x - prev) * (x - mean_hat) - var_hat) / (t + 1)
        if not rejected:
            return lo
        lo = m
    return lo


def binomial_ucb(harms: int, n: int, delta: float = 0.05) -> float:
    """Exact Clopper-Pearson UPPER bound on the harm rate."""
    if n == 0:
        return 1.0
    if harms == n:
        return 1.0
    if harms == 0:
        return 1.0 - delta ** (1.0 / n)
    from scipy.stats import beta
    return float(beta.ppf(1 - delta, harms + 1, n - harms))


def record_allowed_outcome(store: Store, risk: str, harm: bool) -> None:
    crc = store.kv_get(_CRC_KEY, {})
    row = crc.get(risk, {"n": 0, "harms": 0})
    row["n"] += 1
    row["harms"] += int(harm)
    crc[risk] = row
    store.kv_set(_CRC_KEY, crc)


def risk_control_state(store: Store, risk: str) -> dict[str, Any]:
    """Is the ALLOW policy for this risk class inside its harm budget?
    Returns the harm UCB, the budget, and the tightening (added to the
    required success floor) currently in force."""
    row = store.kv_get(_CRC_KEY, {}).get(risk, {"n": 0, "harms": 0})
    n, harms = int(row["n"]), int(row["harms"])
    budget = HARM_BUDGET.get(risk, 0.02)
    ucb = binomial_ucb(harms, n) if n else None
    if ucb is None or n < 10:
        tighten = 0.0            # too little data to certify either direction
        status = "insufficient-data"
    elif ucb <= budget:
        tighten = 0.0
        status = "within-budget"
    else:
        # proportional tightening, capped: shifts marginal traffic to humans
        tighten = min(0.20, 0.5 * (ucb - budget))
        status = "TIGHTENED"
    return {"risk": risk, "n": n, "harms": harms,
            "harm_ucb": round(ucb, 4) if ucb is not None else None,
            "harm_budget": budget, "tighten": round(tighten, 4),
            "status": status}
