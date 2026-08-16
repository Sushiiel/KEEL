"""Custom workspace registry: bring-your-own-data domains.

A workspace is a customer's domain built from THEIR data — no simulator, no
mock anything. The profile stores what only the customer can know (which of
their event types mean customer-visible impact, which are change events,
which take an element hard-down) plus optional runbooks and resolver hints.
Everything else — topology, event history, causal graph, calibration corpus —
arrives through the ingestion API and is learned.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

from ..config import DATA_DIR

_FILE = DATA_DIR / "workspaces.json"
_lock = threading.Lock()

DEFAULT_PROFILE: dict[str, Any] = {
    "outage_types": [],          # event types = hard customer-visible impact
    "degradation_types": [],     # event types = partial impact
    "change_types": [],          # deploys / config pushes / setpoint changes
    "hard_down_types": [],       # events that remove an element from service
    "confounder_types": [],      # symptoms of hidden shared-infrastructure failure
    "runbooks": {},              # event_type -> [action_class, description]
    "action_dynamics": {},       # action_class -> [restore_min, spread, rollback_s, reversible]
    "resolver_hints": {},        # substring/'re:' pattern -> [canonical prefixes]
    "expert_pins": [],
    "auto_verify": False,        # watch mode: verify incidents as they close
    "gap_seconds": 300,          # incident sessionization gap
    "min_events": 4,             # minimum events to open an incident
}


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s[:40] or "workspace"


def _load() -> dict[str, dict[str, Any]]:
    if _FILE.exists():
        try:
            return json.loads(_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save(data: dict[str, dict[str, Any]]) -> None:
    _FILE.write_text(json.dumps(data, indent=2))


def list_workspaces() -> dict[str, dict[str, Any]]:
    with _lock:
        return _load()


def get_workspace(key: str) -> Optional[dict[str, Any]]:
    return list_workspaces().get(key)


def create_workspace(name: str, tenant: str = "", **profile: Any) -> dict[str, Any]:
    with _lock:
        data = _load()
        key = slugify(name)
        base = key
        i = 2
        while key in data:
            key = f"{base}-{i}"
            i += 1
        ws = {"key": key, "name": name, "tenant": tenant or key,
              "created_at": time.time(),
              "profile": {**DEFAULT_PROFILE,
                          **{k: v for k, v in profile.items()
                             if k in DEFAULT_PROFILE}}}
        data[key] = ws
        _save(data)
        return ws


def update_profile(key: str, updates: dict[str, Any]) -> Optional[dict[str, Any]]:
    with _lock:
        data = _load()
        if key not in data:
            return None
        prof = data[key]["profile"]
        for k, v in updates.items():
            if k in DEFAULT_PROFILE:
                prof[k] = v
        _save(data)
        return data[key]


def delete_workspace(key: str) -> bool:
    with _lock:
        data = _load()
        if key not in data:
            return False
        del data[key]
        _save(data)
        return True
