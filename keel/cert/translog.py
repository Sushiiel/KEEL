"""Append-only Merkle transparency log (RFC 6962-style).

Every certificate's canonical hash becomes a leaf; the log root is recomputed
on append and stored on the certificate. Tampering with any historical
certificate breaks the inclusion proof — which is what makes a KEEL
certificate admissible in a postmortem or an audit.
"""
from __future__ import annotations

import hashlib
from typing import Any, Optional

from ..store import Store


def _h(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def leaf_hash(payload: bytes) -> str:
    return _h(b"\x00" + payload)


def _node_hash(left: str, right: str) -> str:
    return _h(b"\x01" + bytes.fromhex(left) + bytes.fromhex(right))


def _root_of(leaves: list[str]) -> str:
    if not leaves:
        return _h(b"")
    level = list(leaves)
    while len(level) > 1:
        nxt = []
        for i in range(0, len(level), 2):
            if i + 1 < len(level):
                nxt.append(_node_hash(level[i], level[i + 1]))
            else:
                nxt.append(level[i])
        level = nxt
    return level[0]


def append(store: Store, payload: bytes, cert_id: str, ts: float
           ) -> tuple[int, str]:
    """Append a leaf; returns (index, new root)."""
    entries = store.translog()
    idx = len(entries)
    lh = leaf_hash(payload)
    store.append_translog(idx, lh, cert_id, ts)
    root = _root_of([e["leaf_hash"] for e in entries] + [lh])
    return idx, root


def current_root(store: Store) -> str:
    return _root_of([e["leaf_hash"] for e in store.translog()])


def inclusion_proof(store: Store, idx: int) -> Optional[dict[str, Any]]:
    entries = store.translog()
    leaves = [e["leaf_hash"] for e in entries]
    if idx < 0 or idx >= len(leaves):
        return None
    proof: list[dict[str, str]] = []
    level = list(leaves)
    pos = idx
    while len(level) > 1:
        nxt = []
        for i in range(0, len(level), 2):
            if i + 1 < len(level):
                nxt.append(_node_hash(level[i], level[i + 1]))
            else:
                nxt.append(level[i])
        sibling = pos ^ 1
        if sibling < len(level):
            proof.append({"hash": level[sibling],
                          "side": "left" if sibling < pos else "right"})
        pos //= 2
        level = nxt
    return {"index": idx, "leaf": leaves[idx], "path": proof,
            "root": _root_of(leaves), "size": len(leaves)}


def verify_inclusion(leaf: str, proof: list[dict[str, str]], root: str) -> bool:
    acc = leaf
    for step in proof:
        if step["side"] == "left":
            acc = _node_hash(step["hash"], acc)
        else:
            acc = _node_hash(acc, step["hash"])
    return acc == root


def verify_chain(store: Store) -> dict[str, Any]:
    """Recompute every leaf from its stored certificate; detect tampering."""
    from .authority import canonical_payload      # local import, no cycle at load
    entries = store.translog()
    bad = []
    for e in entries:
        cert = store.certificate(e["cert_id"])
        if cert is None:
            bad.append({"idx": e["idx"], "reason": "certificate missing"})
            continue
        if leaf_hash(canonical_payload(cert)) != e["leaf_hash"]:
            bad.append({"idx": e["idx"], "reason": "leaf hash mismatch — payload altered"})
    return {"size": len(entries), "root": current_root(store),
            "consistent": not bad, "violations": bad}
