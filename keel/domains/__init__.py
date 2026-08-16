"""Domain packs: everything domain-specific in KEEL lives behind this contract.

The engine — discovery, adjudication, calibration, twin, gate, certificates —
is domain-agnostic. A pack supplies the world: entities and dependency
topology, the event-type vocabulary, the (hidden) generative cascade rules the
demo simulator uses, runbooks, and naming conventions for entity resolution.

Canonical impact schema (every pack MUST follow it):
  - customer-visible impact event types start with  'svc.'  and the SLA-bearing
    product services are entities with ids prefixed  'SVC:'
  - each pack declares which svc.* types are outage-defining vs degradation
  - change/deploy/intervention events use the canonical type  'cfg.push'
    (the domain's own word lives in the raw payload)
  - shared-infrastructure entities use kind 'power' and relation 'feeds' —
    these are the latent-confounder candidates
Relations: 'carries' = provider→consumer dependency, 'feeds' = shared infra,
'peers' = lateral coupling, 'serves' = element→service mapping.

This schema is the same move as the certificate format: a small, opinionated
vocabulary that adapters map into, so one verification engine serves every
industry.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np


@dataclass
class Scenario:
    key: str
    title: Callable[[str], str]
    severity: str
    root_type: str
    pick_entity: Callable[[np.random.Generator], str]
    noise: tuple[int, int] = (5, 11)
    dup: tuple[int, int] = (2, 5)
    hidden_root: bool = False       # root emits no alarm (monitoring gap)
    config_error: bool = False      # boosts the cfg.push excitation (bad change)


@dataclass
class DomainPack:
    key: str
    name: str                        # display name, e.g. "Telecom · metro transport"
    tenant: str                      # default tenant id for certificates
    world_title: str                 # map panel caption
    icon: str                        # small glyph for the UI switcher

    build_world: Callable[[Any], None]            # (store) -> entities + topology
    # (src_type, dst_type, selector, probability, delay_lo_s, delay_hi_s)
    true_rules: list[tuple[str, str, str, float, float, float]]
    severity: dict[str, int]
    layer_of_type: dict[str, str]
    noise_types: list[str]
    scenarios: dict[str, Scenario]
    scenario_weights: dict[str, float]
    # canonical open incident: dict(incident_id, scenario, root_entity, title, seed)
    canonical: dict[str, Any]

    # impact vocabulary (all names must start with 'svc.')
    outage_types: set[str]           # OUTCOME parents at w=0.99
    degradation_types: set[str]      # OUTCOME parents at w=0.80
    impact_protected_type: str       # emitted when a backup path absorbs a hit
    impact_outage_type: str          # emitted when it does not

    # actuation
    runbooks: dict[str, tuple[str, str]]          # event_type -> (class, description)
    action_dynamics: dict[str, tuple[float, float, float, bool]]
    hard_down_types: set[str]        # events that truly remove an element
    confounder_types: set[str]       # types a hidden shared-infra failure causes

    # entity resolution
    raw_namer: Callable[[str], list[str]]         # canonical id -> raw aliases
    resolver_hints: dict[str, tuple[str, ...]]    # substring -> canonical prefixes
    site_token: Callable[[str], set[str]]         # raw/canonical -> site tokens

    expert_pins: list[dict[str, Any]] = field(default_factory=list)
    history_incidents: int = 150
    history_days: int = 90
    seed: int = 7
    synthetic: bool = True                 # False = BYO-data workspace (no simulator)
    change_types: set[str] = field(default_factory=lambda: {"cfg.push"})
    auto_verify: bool = False              # watch mode
    gap_seconds: float = 300.0             # incident sessionization gap
    min_events: int = 4

    @property
    def impact_types(self) -> set[str]:
        extra = {self.impact_protected_type} if self.impact_protected_type else set()
        return self.outage_types | self.degradation_types | extra


_REGISTRY: dict[str, DomainPack] = {}


def register(pack: DomainPack) -> DomainPack:
    _REGISTRY[pack.key] = pack
    return pack


def get_pack(key: str) -> DomainPack:
    _load_all()
    if key in _REGISTRY:
        from ..config import SANDBOX_ENABLED
        if not SANDBOX_ENABLED:
            raise KeyError(f"sandbox domain '{key}' is disabled — set "
                           "KEEL_SANDBOX=1 to enable demo worlds")
        return _REGISTRY[key]
    from .registry import get_workspace
    ws = get_workspace(key)
    if ws is not None:
        from .custom import build_custom_pack
        return build_custom_pack(ws)
    raise KeyError(f"unknown domain '{key}'; available: {sorted(_REGISTRY)}")


def all_packs() -> dict[str, DomainPack]:
    """Built-in sandbox packs only; workspaces are enumerated separately.
    Empty unless sandbox mode is explicitly enabled — no mock data ships on."""
    from ..config import SANDBOX_ENABLED
    if not SANDBOX_ENABLED:
        return {}
    _load_all()
    return dict(_REGISTRY)


_loaded = False


def _load_all() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    from . import telecom, cloud, grid, manufacturing  # noqa: F401


DEFAULT_DOMAIN = "telecom"
