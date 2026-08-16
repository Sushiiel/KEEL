"""Custom DomainPack built from a workspace profile — the BYO-data path.

No simulator, no scenarios, no mock world. The pack carries only the
customer's declared semantics; topology and events arrive via ingestion,
structure is learned, calibration is earned from their labeled outcomes.
"""
from __future__ import annotations

import re
from typing import Any

from . import DomainPack


def _generic_site_token(s: str) -> set[str]:
    """Trailing alphanumeric zone/site discriminators: 'db-us-east-1a' -> {'1a'}."""
    return set(re.findall(r"[-_/.](\d+[a-z]?|[a-z]\d+)$", s.lower()))


def build_custom_pack(ws: dict[str, Any]) -> DomainPack:
    p = ws["profile"]
    return DomainPack(
        key=ws["key"],
        name=ws["name"],
        tenant=ws.get("tenant", ws["key"]),
        world_title=f"{ws['name']} — connected data",
        icon="◆",
        build_world=lambda store: None,          # the customer's data IS the world
        true_rules=[],                           # reality generates the events
        severity={},
        layer_of_type={},
        noise_types=[],
        scenarios={},
        scenario_weights={},
        canonical={},
        outage_types=set(p.get("outage_types", [])),
        degradation_types=set(p.get("degradation_types", [])),
        impact_protected_type="",
        impact_outage_type=(next(iter(p.get("outage_types", [])), "")),
        runbooks={k: tuple(v) for k, v in p.get("runbooks", {}).items()},
        action_dynamics={k: tuple(v) for k, v in
                         p.get("action_dynamics", {}).items()},
        hard_down_types=set(p.get("hard_down_types", [])),
        confounder_types=set(p.get("confounder_types", [])),
        raw_namer=lambda e: [e],
        resolver_hints={k: tuple(v) for k, v in
                        p.get("resolver_hints", {}).items()},
        site_token=_generic_site_token,
        expert_pins=list(p.get("expert_pins", [])),
        synthetic=False,
        change_types=set(p.get("change_types", [])) or {"cfg.push"},
        auto_verify=bool(p.get("auto_verify", False)),
        gap_seconds=float(p.get("gap_seconds", 300)),
        min_events=int(p.get("min_events", 4)),
    )
