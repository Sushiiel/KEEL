"""KEEL — the causal verification layer for autonomous operations.

KEEL sits between an AI agent and production infrastructure. Given an incident
and a claimed root cause, it adjudicates the claim against a learned structural
causal model (probability of necessity / sufficiency, with Tian-Pearl bounds
when not identified), calibrates the result with conformal prediction against a
per-tenant corpus of resolved incidents, simulates proposed remediations in a
digital twin, checks them against a CMDP safety shield and a policy layer, and
emits a signed, machine-readable Causal Certificate — or abstains.

The LLM proposes hypotheses. It never adjudicates truth.
"""

__version__ = "0.3.0"
