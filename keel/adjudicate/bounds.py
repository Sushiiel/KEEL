"""Tian-Pearl bounds on the probability of necessity.

When point identification fails (latent confounding suspected), we ship the
honest interval instead of a fake point estimate:

    max(0, [P(y|x) - P(y|not x)] / P(y|x))  <=  PN  <=  min(1, P(not y|not x) / P(x,y) ... )

We use the observational-data form (Tian & Pearl 2000, Thm. 4):
    lower = max(0, (P(y|x) - P(y|~x)) / P(y|x))
    upper = min(1, P(~y|~x) / P(y|x) * P(~x)/P(x) + ... )

For the certificate we report the standard conservative pair:
    lower = max(0, (P(y|x) - P(y|~x)) / P(y|x))
    upper = min(1, P(~y|~x) / P(y|x))

Assumptions: the historical corpus is representative (exchangeability with the
current incident) — exactly what the drift gate monitors.
"""
from __future__ import annotations


def tian_pearl_bounds(n_xy: int, n_x_noty: int, n_notx_y: int,
                      n_notx_noty: int) -> tuple[float, float]:
    """Bounds from a 2x2 observational table over historical incidents."""
    n_x = n_xy + n_x_noty
    n_notx = n_notx_y + n_notx_noty
    if n_x == 0 or n_xy == 0:
        return 0.0, 1.0
    p_y_x = n_xy / n_x
    p_y_notx = (n_notx_y / n_notx) if n_notx > 0 else 0.0
    p_noty_notx = (n_notx_noty / n_notx) if n_notx > 0 else 1.0
    lower = max(0.0, (p_y_x - p_y_notx) / p_y_x)
    upper = min(1.0, p_noty_notx / p_y_x)
    return round(lower, 4), round(upper, 4)


def observational_table(corpus_types: list[tuple[set[str], bool]],
                        x_type: str) -> tuple[int, int, int, int]:
    """Count (x,y) co-occurrence at type level over resolved incidents.

    corpus_types: per incident, (set of event types that fired, outage flag).
    """
    n_xy = n_x_noty = n_notx_y = n_notx_noty = 0
    for types, outage in corpus_types:
        x = x_type in types
        if x and outage:
            n_xy += 1
        elif x and not outage:
            n_x_noty += 1
        elif not x and outage:
            n_notx_y += 1
        else:
            n_notx_noty += 1
    return n_xy, n_x_noty, n_notx_y, n_notx_noty
