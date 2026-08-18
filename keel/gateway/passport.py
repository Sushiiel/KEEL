"""Agent passports: portable, verifiable trust records.

The cold-start problem is where every trust system quietly fails: an agent
with ten thousand externally-verified outcomes behind one KEEL deployment
starts from zero the moment it is deployed anywhere else. Its operator either
re-earns trust from scratch (weeks of escalations) or the new operator skips
the ramp entirely (the thing KEEL exists to prevent).

A passport is the earned calibration record — per action class: n, successes,
harms, externally-verified count — signed by the issuing deployment's Ed25519
authority and bound to its transparency-log state. Any other deployment can
verify it offline and adopt it as a DISCOUNTED prior.

The statistical honesty rules, which are the point:

  - Imported evidence is discounted (default 50%) and capped, then run through
    the same Clopper-Pearson lower bound as local evidence. A foreign record
    is real Bernoulli evidence from a DIFFERENT environment; the discount is
    the price of the transfer.
  - A passport can bridge cold start for MEDIUM risk only. High and critical
    actions still require locally earned evidence and tiers — a track record
    from someone else's environment is never enough to touch what matters.
  - Local autonomy tiers are NEVER imported. Tier promotion requires local
    externally-verified outcomes, exactly as before.
  - A passport whose record includes ANY harm confers no benefit. It can still
    be adopted — the history is true and worth recording — but it buys nothing.
  - Adoption requires the issuer's public key out-of-band. Verifying a
    passport against the key embedded in the passport itself proves only that
    somebody signed it.
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from ..cert import authority
from .checkers import clopper_pearson_lower
from .engine import (_STRATA, _stratum_key, get_agent, gw_store,
                     register_agent)
from .models import ActionClassSpec, AgentProfile

SCHEMA = "keel-passport/v1"
VALID_DAYS = 90            # a stale record must be reissued, not trusted forever
DEFAULT_DISCOUNT = 0.5     # transfer price of evidence from a foreign environment
DEFAULT_CAP_N = 50         # imported pseudo-counts never dwarf local evidence


def _canonical(p: dict[str, Any]) -> bytes:
    data = {k: v for k, v in p.items() if k != "signature"}
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


# ── issuing ──────────────────────────────────────────────────────────────────

def issue_passport(agent_id: str) -> Optional[dict[str, Any]]:
    """Sign the agent's earned record for presentation elsewhere. None if the
    agent does not exist. Reads the raw strata (full history), not the drift
    window — the receiver applies its own discounting."""
    agent = get_agent(agent_id)
    if agent is None:
        return None
    from ..cert import translog
    store = gw_store()
    strata = store.kv_get(_STRATA, {})
    record = []
    for cls, spec in agent.action_classes.items():
        s = strata.get(_stratum_key(agent_id, cls), {})
        record.append({
            "action_class": cls, "risk": spec.risk,
            "n": int(s.get("n", 0)), "successes": int(s.get("successes", 0)),
            "harms": int(s.get("harms", 0)),
            "n_external": int(s.get("n_external", 0)),
        })
    now = time.time()
    passport = {
        "schema": SCHEMA,
        "agent": {"agent_id": agent.agent_id, "name": agent.name,
                  "framework": agent.framework},
        "record": sorted(record, key=lambda r: r["action_class"]),
        "issuer": {
            "signer": authority.SIGNER_ID,
            "public_key": authority.public_key_hex(),
            # binds the passport to a log state the issuer can be held to via
            # its signed checkpoints
            "log_root": translog.current_root(store),
            "log_size": len(store.translog()),
        },
        "issued_at": now,
        "expires_at": now + VALID_DAYS * 86400,
    }
    passport["signature"] = authority.signing_key().sign(_canonical(passport)).hex()
    return passport


# ── verifying (pure — no store, usable offline) ──────────────────────────────

def verify_passport(passport: dict[str, Any],
                    issuer_key_hex: str = "") -> dict[str, Any]:
    """Verify a passport offline. `issuer_key_hex` is the key you obtained
    OUT-OF-BAND; empty means fall back to the embedded key, which is flagged
    because a self-vouching document proves only its own consistency."""
    from ..cert.verifier import verify_signature as _vs
    report: dict[str, Any] = {"checks": {}, "valid": False}
    if passport.get("schema") != SCHEMA:
        report["error"] = f"unknown schema {passport.get('schema')!r}"
        return report
    # the shared canonicalization excludes the certificate-family fields
    # log_index/log_root from signing; a passport never legitimately carries
    # them, so their presence is an unsigned smuggling channel — refuse it
    if "log_index" in passport or "log_root" in passport:
        report["error"] = "unexpected certificate-family fields in passport"
        return report
    key = (issuer_key_hex or "").strip()
    report["key_pinned"] = bool(key)
    if not key:
        key = str((passport.get("issuer") or {}).get("public_key", "")).strip()
    # the verifier's canonicalization (sorted keys, compact, signature-family
    # fields excluded) matches _canonical exactly for this shape, so the
    # certificate signature check applies verbatim
    sig_ok = _vs(passport, key)
    report["checks"]["signature"] = sig_ok
    expired = time.time() > float(passport.get("expires_at", 0))
    report["checks"]["not_expired"] = not expired
    report["agent_id"] = (passport.get("agent") or {}).get("agent_id", "")
    report["valid"] = sig_ok and not expired
    return report


# ── adopting ─────────────────────────────────────────────────────────────────

def adopt_passport(passport: dict[str, Any], issuer_key_hex: str,
                   owner_account: str = "",
                   discount: float = DEFAULT_DISCOUNT,
                   cap_n: int = DEFAULT_CAP_N) -> dict[str, Any]:
    """Verify and adopt a foreign track record as a discounted prior.

    Fail-closed rules: the issuer key must be supplied explicitly (out-of-band
    trust decision, not the passport vouching for itself), the signature must
    verify, and the passport must be current. The agent is registered in
    shadow mode if unknown — a passport accelerates calibration, it never
    skips enforcement setup.
    """
    if not issuer_key_hex.strip():
        return {"adopted": False,
                "error": "issuer public key required — obtain it from the "
                         "issuing deployment out-of-band; a passport must not "
                         "vouch for itself"}
    rep = verify_passport(passport, issuer_key_hex)
    if not rep["valid"]:
        return {"adopted": False, "error": "passport failed verification",
                "report": rep}

    agent_id = passport["agent"]["agent_id"]
    existing = get_agent(agent_id)
    if existing is not None and owner_account and \
            (existing.owner_account or "") != owner_account:
        # a valid passport for someone ELSE'S agent must not let the importer
        # alter that agent's decision behaviour — adoption writes a prior into
        # the very strata decide() consults
        return {"adopted": False,
                "error": "an agent with this id belongs to another account; "
                         "a passport cannot be adopted onto it"}
    if existing is None:
        register_agent(AgentProfile(
            agent_id=agent_id, name=passport["agent"].get("name", agent_id),
            framework=passport["agent"].get("framework", ""),
            owner_account=owner_account, shadow_mode=True,
            action_classes={r["action_class"]: ActionClassSpec(
                name=r["action_class"], risk=r["risk"])
                for r in passport.get("record", [])}))

    store = gw_store()
    strata = store.kv_get(_STRATA, {})
    issuer_prefix = passport["issuer"]["public_key"][:16]
    adopted = []
    for r in passport.get("record", []):
        # clamp before discounting: a crafted record with negative or
        # successes>n counts must degrade to a null prior, never to nonsense
        src_n = max(0, int(r["n"]))
        src_s = max(0, min(src_n, int(r["successes"])))
        n_eff = min(cap_n, int(src_n * discount))
        s_eff = min(n_eff, int(src_s * discount))
        key = _stratum_key(agent_id, r["action_class"])
        row = strata.get(key, {})
        row["passport"] = {
            "n_eff": n_eff, "successes_eff": s_eff,
            "src_n": r["n"], "src_successes": r["successes"],
            "harms": int(r["harms"]), "n_external_src": int(r["n_external"]),
            "issuer": issuer_prefix, "discount": discount,
            "issued_at": passport["issued_at"], "adopted_at": time.time(),
        }
        strata[key] = row
        adopted.append({"action_class": r["action_class"], "n_eff": n_eff,
                        "successes_eff": s_eff, "harms": int(r["harms"])})
    store.kv_set(_STRATA, strata)
    return {"adopted": True, "agent_id": agent_id, "issuer": issuer_prefix,
            "strata": adopted,
            "note": "imported evidence is a discounted prior: it can bridge "
                    "cold start for medium-risk actions only; tiers and "
                    "high/critical autonomy must be earned locally"}
