"""Domain-free world helpers over the store."""
from __future__ import annotations

from ..store import Store


def service_paths(store: Store) -> dict[str, list[list[str]]]:
    return {e.entity_id: e.attrs.get("paths", [])
            for e in store.entities() if e.kind == "service"}


def service_customers(store: Store) -> dict[str, int]:
    return {e.entity_id: int(e.attrs.get("customers", 0))
            for e in store.entities() if e.kind == "service"}
