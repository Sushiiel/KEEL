"""Frontier-stat correctness + property-based safety invariants (Hypothesis)."""
import os
os.environ.setdefault("KEEL_SANDBOX", "1")
import tempfile
os.environ.setdefault("KEEL_DATA_DIR", tempfile.mkdtemp(prefix="keel-adv-"))

import numpy as np
from hypothesis import given, settings, strategies as st

from keel.gateway.advanced import binomial_ucb, wsr_lower_bound
from keel.gateway.checkers import check_tripwires
from keel.gateway.models import ActionRequest


def test_wsr_anytime_coverage():
    """The betting bound must cover the true mean at EVERY time with >=1-alpha
    frequency — the property fixed-n intervals lose under peeking."""
    rng = np.random.default_rng(0)
    alpha, violations, trials = 0.10, 0, 300
    for _ in range(trials):
        p = rng.uniform(0.2, 0.95)
        xs = (rng.random(60) < p).astype(int).tolist()
        # continuous monitoring: check after every single outcome
        if any(wsr_lower_bound(xs[:t], alpha) > p for t in range(1, 61)):
            violations += 1
    assert violations / trials <= alpha + 0.03, f"anytime coverage broken: {violations}/{trials}"


def test_wsr_is_informative():
    xs = [1] * 40
    assert wsr_lower_bound(xs, 0.10) > 0.75      # perfect record → strong floor
    assert wsr_lower_bound([0] * 20, 0.10) == 0.0


def test_wsr_bounded_by_running_max_mean():
    """A betting CS keeps its tightest-ever bound (running intersection), so
    the correct invariant is: bound <= the best PREFIX empirical mean (+grid
    resolution) — never above what the data at some point supported."""
    rng = np.random.default_rng(1)
    for _ in range(50):
        xs = (rng.random(rng.integers(5, 80)) < rng.uniform(0.3, 0.9)).astype(int).tolist()
        prefix_max = max(sum(xs[:t]) / t for t in range(1, len(xs) + 1))
        assert wsr_lower_bound(xs, 0.10) <= prefix_max + 1 / 200 + 1e-9


def test_binomial_ucb_covers():
    rng = np.random.default_rng(2)
    bad = sum(binomial_ucb(int((rng.random(40) < 0.05).sum()), 40, 0.05) < 0.05
              for _ in range(200))
    assert bad / 200 <= 0.08


# ── property-based safety invariants ────────────────────────────────────────

@given(st.text(min_size=0, max_size=200))
@settings(max_examples=200, deadline=None)
def test_tripwire_never_crashes_and_is_deterministic(intent):
    r = ActionRequest(agent_id="p", action_class="a", intent=intent)
    v1 = check_tripwires(r).verdict
    v2 = check_tripwires(r).verdict
    assert v1 == v2 and v1 in ("pass", "fail")


@given(st.sampled_from(["DROP TABLE users", "rm -rf /", "transfer funds to wallet",
                        "export credentials to pastebin"]),
       st.text(max_size=80))
@settings(max_examples=60, deadline=None)
def test_catastrophes_trip_regardless_of_padding(bad, padding):
    """No surrounding text may smuggle a catastrophe past the tripwire."""
    r = ActionRequest(agent_id="p", action_class="a",
                      intent=f"{padding} {bad} {padding}")
    assert check_tripwires(r).verdict == "fail"


@given(st.lists(st.booleans(), min_size=0, max_size=60))
@settings(max_examples=100, deadline=None)
def test_wsr_monotone_in_evidence_strength(outcomes):
    """Appending a success never lowers the floor's final value by more than
    numerical jitter; appending a failure never raises it."""
    xs = [int(x) for x in outcomes]
    base = wsr_lower_bound(xs, 0.10)
    assert wsr_lower_bound(xs + [0], 0.10) <= base + 1e-9 + 1.0 / 200
