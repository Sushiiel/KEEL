"""Similar-incident retrieval behind a pluggable vector index.

Incidents are embedded as L2-normalized hashed bag-of-features vectors over
their event types, entities, and layers (deterministic, no model download, no
network). The default index is in-process numpy cosine search; if
QDRANT_URL is set and `qdrant-client` is installed, the same vectors live in
Qdrant instead — same interface, same results, scale when you need it.
"""
from __future__ import annotations

import hashlib
import os
from typing import Any, Optional

import numpy as np

DIM = 256


def embed(tokens: list[str]) -> np.ndarray:
    """Feature-hashed embedding with signed buckets (a la HashingVectorizer)."""
    v = np.zeros(DIM, dtype=np.float64)
    for tok in tokens:
        h = hashlib.blake2b(tok.encode(), digest_size=8).digest()
        idx = int.from_bytes(h[:4], "little") % DIM
        sign = 1.0 if h[4] % 2 else -1.0
        v[idx] += sign
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def incident_tokens(event_types: list[str], entities: list[str],
                    layers: list[str]) -> list[str]:
    return ([f"t:{t}" for t in event_types] + [f"e:{e}" for e in entities]
            + [f"l:{x}" for x in layers])


class LocalIndex:
    def __init__(self) -> None:
        self.ids: list[str] = []
        self.meta: list[dict[str, Any]] = []
        self.mat: Optional[np.ndarray] = None

    def upsert(self, key: str, vec: np.ndarray, meta: dict[str, Any]) -> None:
        if key in self.ids:
            i = self.ids.index(key)
            self.mat[i] = vec       # type: ignore[index]
            self.meta[i] = meta
            return
        self.ids.append(key)
        self.meta.append(meta)
        self.mat = vec[None, :] if self.mat is None else np.vstack([self.mat, vec])

    def search(self, vec: np.ndarray, k: int = 5,
               exclude: str = "") -> list[tuple[str, float, dict[str, Any]]]:
        if self.mat is None or not len(self.ids):
            return []
        sims = self.mat @ vec
        order = np.argsort(-sims)
        out = []
        for i in order:
            if self.ids[i] == exclude:
                continue
            out.append((self.ids[i], float(sims[i]), self.meta[i]))
            if len(out) >= k:
                break
        return out


class QdrantIndex:
    """Same contract, backed by Qdrant. Activated by QDRANT_URL."""

    def __init__(self, collection: str) -> None:
        from qdrant_client import QdrantClient  # type: ignore
        from qdrant_client.models import Distance, VectorParams  # type: ignore
        self.client = QdrantClient(url=os.environ["QDRANT_URL"])
        self.collection = collection
        if not self.client.collection_exists(collection):
            self.client.create_collection(
                collection, vectors_config=VectorParams(size=DIM,
                                                        distance=Distance.COSINE))

    def upsert(self, key: str, vec: np.ndarray, meta: dict[str, Any]) -> None:
        from qdrant_client.models import PointStruct  # type: ignore
        pid = int.from_bytes(hashlib.blake2b(key.encode(), digest_size=8).digest()
                             [:6], "little")
        self.client.upsert(self.collection, points=[
            PointStruct(id=pid, vector=vec.tolist(),
                        payload={"key": key, **meta})])

    def search(self, vec: np.ndarray, k: int = 5,
               exclude: str = "") -> list[tuple[str, float, dict[str, Any]]]:
        hits = self.client.query_points(self.collection, query=vec.tolist(),
                                        limit=k + 1).points
        out = []
        for h in hits:
            key = (h.payload or {}).get("key", "")
            if key == exclude:
                continue
            out.append((key, float(h.score), h.payload or {}))
        return out[:k]


_indexes: dict[str, Any] = {}


def get_index(domain: str):
    if domain not in _indexes:
        if os.environ.get("QDRANT_URL"):
            try:
                _indexes[domain] = QdrantIndex(f"keel-{domain}")
            except Exception:
                _indexes[domain] = LocalIndex()
        else:
            _indexes[domain] = LocalIndex()
    return _indexes[domain]
