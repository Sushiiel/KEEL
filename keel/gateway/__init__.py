"""KEEL Gateway — the universal runtime trust layer for agentic AI.

Any AI product, in any field, submits a proposed action or claim BEFORE
executing it. The gateway verifies (deterministic checkers + calibrated
confidence from that agent's own outcome history), decides
ALLOW / BLOCK / ESCALATE / ABSTAIN by risk class and earned autonomy tier,
and signs the decision into a Merkle transparency log. Outcomes reported back
close the calibration loop.

The LLM inside the client product proposes. It never adjudicates its own
trustworthiness — that inversion is the whole design, generalized from
KEEL's causal-verification core to every agentic system.
"""
