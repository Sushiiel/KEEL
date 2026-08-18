"""Central configuration: paths, statistical thresholds, autonomy tiers.

Every threshold that gates a decision lives here so an auditor can read the
whole risk posture of a deployment in one file.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PKG = Path(__file__).resolve().parent            # the installed keel/ package
ROOT = PKG.parent                                # repo root (dev) or site-packages
# Static assets ship inside the package so `pip install keel-trust` is self-contained.
UI_DIR = PKG / "ui"
SCHEMA_DIR = PKG / "schemas"
SITE_DIR = PKG / "site"
# Writable data defaults to the user's home so an installed library needs no
# repo; override with KEEL_DATA_DIR (a repo-local ./data wins if it exists).
_dev_data = ROOT / "data"
DATA_DIR = Path(os.environ.get(
    "KEEL_DATA_DIR",
    _dev_data if _dev_data.exists() else Path.home() / ".keel" / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "keel.db"
KEY_PATH = DATA_DIR / "keel_signing_key.pem"

# Sandbox demo worlds (simulated industries) are OPT-IN only. The shipped
# default is a clean product: your workspaces, your agents, your data.
SANDBOX_ENABLED = os.environ.get("KEEL_SANDBOX", "0") == "1"

TENANT = os.environ.get("KEEL_TENANT", "default")
SIGNER_ID = os.environ.get("KEEL_SIGNER", "keel-authority-prod")

# ── Statistical configuration ────────────────────────────────────────────────
CONFORMAL_ALPHA = 0.10          # target miscoverage for hypothesis sets
MIN_CALIBRATION_N = 25          # below this the calibrator refuses to certify
ABDUCTION_SAMPLES = 4000        # posterior samples for counterfactual engine
BOOTSTRAP_ROUNDS = 60           # CI bootstrap for PN/PS
STABILITY_BOOTSTRAPS = 24       # causal-discovery stability selection resamples
STABILITY_KEEP = 0.60           # edge kept if selected in >= this fraction
DISCOVERY_WINDOW_MS = 90_000    # max lag considered for excitation edges

# ── Drift gate ───────────────────────────────────────────────────────────────
DRIFT_ENERGY_WARN = 0.15        # energy distance: widen intervals
DRIFT_ENERGY_BREACH = 0.30      # energy distance: abstain
DRIFT_GED_BREACH = 0.35         # normalized graph edit distance between versions
FIDELITY_FLOOR = 0.70           # refuse to certify action classes below this


@dataclass(frozen=True)
class AutonomyTier:
    tier: int
    name: str
    behavior: str
    pn_lower_min: float
    max_blast_elements: int
    max_slas_at_risk: int
    requires_reversible: bool
    min_prior_successes: int


AUTONOMY_TIERS: dict[int, AutonomyTier] = {
    0: AutonomyTier(0, "T0 · Observe", "certify only, never recommend execution", 0.0, 0, 0, False, 0),
    1: AutonomyTier(1, "T1 · Recommend", "certify + recommend, human executes", 0.60, 40, 1, False, 0),
    2: AutonomyTier(2, "T2 · Auto-execute, notify", "auto-execute with notification", 0.80, 20, 0, True, 3),
    3: AutonomyTier(3, "T3 · Auto-execute, silent", "fully autonomous within envelope", 0.95, 10, 0, True, 8),
}

# CMDP constraint limits (the "physics" side of the gate; policy is separate)
CMDP_LIMITS = {
    "elements_touched": 12,
    "blast_radius_elements": 25,
    "slas_at_risk": 0,
    "redundancy_min_paths": 1,   # never drop a service below this many live paths
    "est_sla_minutes": 5.0,
}

CHANGE_WINDOWS = [(0, 6), (22, 24)]  # local hours where changes are allowed


def in_change_window(hour: int) -> bool:
    return any(lo <= hour < hi for lo, hi in CHANGE_WINDOWS)
