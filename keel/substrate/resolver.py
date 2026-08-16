"""Entity resolution: map vendor-specific raw alarm names to canonical IDs.

Unglamorous, and 40% of why AIOps deployments fail. If two spellings of one
element become two nodes, causal discovery learns confident nonsense — so the
resolver ships with its own measured precision harness (target >= 0.98) and an
unresolved queue for manual reconciliation instead of silent guessing.

Method: normalization → structural blocking on (kind hints, site token) from
the domain pack → trigram similarity with margin acceptance → alias cache.
Hint keys starting with 're:' are regex patterns; everything else is a
substring match.
"""
from __future__ import annotations

import re
from typing import Optional

from ..domains import DomainPack
from ..store import Store

_ACCEPT = 0.62
_MARGIN = 0.10


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _trigrams(s: str) -> set[str]:
    s = f"  {s} "
    return {s[i:i + 3] for i in range(len(s) - 2)}


def _sim(a: str, b: str) -> float:
    ta, tb = _trigrams(_norm(a)), _trigrams(_norm(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


class EntityResolver:
    def __init__(self, store: Store, pack: DomainPack):
        self.store = store
        self.pack = pack
        self.canonical = [e.entity_id for e in store.entities()]
        self.cache: dict[str, str] = {r: eid for r, (eid, _, _) in store.aliases().items()}
        self.unresolved: list[str] = []

    def _kind_prefixes(self, raw: str) -> tuple[str, ...]:
        low = raw.lower()
        hits: tuple[str, ...] = ()
        for hint, prefixes in self.pack.resolver_hints.items():
            if hint.startswith("re:"):
                if re.search(hint[3:], low):
                    hits += prefixes
            elif hint in low:
                hits += prefixes
        return hits

    def resolve(self, raw: str) -> Optional[str]:
        if raw in self.canonical:
            return raw
        if raw in self.cache:
            return self.cache[raw]
        got, conf, method = self._structural(raw)
        if got is None:
            got, conf, method = self._fuzzy(raw)
        if got is not None:
            self.cache[raw] = got
            self.store.put_alias(raw, got, method, round(conf, 3))
            return got
        self.unresolved.append(raw)
        return None

    def _structural(self, raw: str) -> tuple[Optional[str], float, str]:
        prefixes = self._kind_prefixes(raw)
        sites = self.pack.site_token(raw)
        if not prefixes:
            return None, 0.0, ""
        pool = [c for c in self.canonical if c.startswith(prefixes)]
        if sites:
            pool = [c for c in pool if self.pack.site_token(c) & sites]
        if len(pool) == 1:
            return pool[0], 0.92, "structural:kind+site"
        # trailing-number discriminator (spans, feeders, conveyors, presses)
        m = re.search(r"(\d+)\s*$", raw.lower())
        if m and len(pool) > 1:
            tail = [c for c in pool if re.search(rf"[-_]?0?{m.group(1)}$", c)]
            if len(tail) == 1:
                return tail[0], 0.90, "structural:kind+ordinal"
        return None, 0.0, ""

    def _fuzzy(self, raw: str) -> tuple[Optional[str], float, str]:
        sites = self.pack.site_token(raw)
        prefixes = self._kind_prefixes(raw)
        pool = [c for c in self.canonical
                if (not prefixes or c.startswith(prefixes))
                and (not sites or (self.pack.site_token(c) & sites)
                     or not self.pack.site_token(c))]
        if not pool:
            pool = self.canonical
        scored = sorted(((self._score(raw, c, sites), c) for c in pool), reverse=True)
        best, second = scored[0], (scored[1] if len(scored) > 1 else (0.0, ""))
        if best[0] >= _ACCEPT and best[0] - second[0] >= _MARGIN:
            return best[1], best[0], "trigram+blocking"
        return None, 0.0, ""

    def _score(self, raw: str, cand: str, sites: set[str]) -> float:
        s = _sim(raw, cand)
        cand_sites = self.pack.site_token(cand)
        if sites and cand_sites:
            s += 0.25 if (sites & cand_sites) else -0.35
        return s

    def audit(self, truth: dict[str, str]) -> dict[str, float]:
        tp = fp = fn = 0
        for raw, want in truth.items():
            got = self.resolve(raw)
            if got is None:
                fn += 1
            elif got == want:
                tp += 1
            else:
                fp += 1
        total = len(truth) or 1
        return {"precision": tp / max(tp + fp, 1), "recall": tp / max(tp + fn, 1),
                "coverage": (tp + fp) / total, "n": total,
                "unresolved": fn, "wrong": fp}
