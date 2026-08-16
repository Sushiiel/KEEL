"""A2A-style interop: signed Agent Card + JSON-RPC skill surface.

Any vendor's agent can submit a causal claim and receive a signed certificate.
The certificate is the governance artifact the agent protocols cannot express
themselves — KEEL's strategic wedge. Skills advertised:

  verify_causal_claim          incident + claimed root cause -> certificate
  request_execution_certificate  execute a SUPPORTED certificate's action
  get_certificate              fetch + verify any issued certificate
"""
from __future__ import annotations

import json
from typing import Any

from ..cert import authority, translog
from ..config import SIGNER_ID, TENANT
from ..pipeline import Runtime, execute_certificate, run_verification


def agent_card(base_url: str) -> dict[str, Any]:
    card = {
        "protocolVersion": "1.0.1",
        "name": "keel-causal-authority",
        "description": ("KEEL — causal verification layer. Adjudicates root-cause "
                        "claims against a learned structural causal model and "
                        "issues signed, conformally-calibrated Causal Certificates."),
        "url": f"{base_url}/a2a",
        "provider": {"organization": TENANT, "signer": SIGNER_ID},
        "capabilities": {"streaming": True, "certificates": "keel-certificate/v1"},
        "skills": [
            {"id": "verify_causal_claim",
             "name": "Verify a causal claim",
             "description": "Adjudicate a claimed root cause for an incident; "
                            "returns a signed Causal Certificate or an abstention.",
             "inputSchema": {"incident_id": "string",
                             "claim_variable": "entity|event_type",
                             "claimant": "string"}},
            {"id": "request_execution_certificate",
             "name": "Request execution of a certified action",
             "description": "Execute the remediation on a SUPPORTED certificate "
                            "through the CMDP shield and policy gate."},
            {"id": "get_certificate",
             "name": "Fetch and verify a certificate",
             "description": "Returns the certificate, its signature check, and "
                            "its Merkle inclusion proof."},
        ],
        "publicKey": {"alg": "Ed25519", "hex": authority.public_key_hex()},
    }
    # sign the card itself
    payload = json.dumps(card, sort_keys=True, separators=(",", ":")).encode()
    card["signature"] = authority.signing_key().sign(payload).hex()
    return card


def handle_rpc(rt: Runtime, body: dict[str, Any]) -> dict[str, Any]:
    """Minimal JSON-RPC 2.0 dispatcher for the A2A skill surface."""
    rpc_id = body.get("id")
    method = body.get("method", "")
    params = body.get("params", {}) or {}

    def ok(result: Any) -> dict:
        return {"jsonrpc": "2.0", "id": rpc_id, "result": result}

    def err(code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": rpc_id,
                "error": {"code": code, "message": message}}

    try:
        if method == "verify_causal_claim":
            steps = list(run_verification(
                rt, params.get("incident_id", ""),
                claim_variable=params.get("claim_variable"),
                claimant=params.get("claimant", "external-agent")))
            cert_step = next((s for s in steps if s["stage"] == "certificate"), None)
            if cert_step is None:
                fail = next((s for s in steps if s["status"] == "failed"), None)
                return err(-32004, fail["detail"] if fail else "verification failed")
            return ok({"certificate": cert_step["data"]["certificate"],
                       "stages": [{"stage": s["stage"], "detail": s["detail"]}
                                  for s in steps]})

        if method == "request_execution_certificate":
            res = execute_certificate(rt, params.get("cert_id", ""),
                                      approver=params.get("approver",
                                                          "external-agent"))
            if "error" in res:
                return err(-32005, res["error"])
            return ok(res)

        if method == "get_certificate":
            cert = rt.store.certificate(params.get("cert_id", ""))
            if cert is None:
                return err(-32006, "unknown certificate")
            proof = (translog.inclusion_proof(rt.store, cert.log_index)
                     if cert.log_index is not None else None)
            return ok({"certificate": cert.model_dump(),
                       "verification": authority.verify(cert),
                       "inclusion_proof": proof})

        return err(-32601, f"unknown method {method}")
    except Exception as e:                                    # fail closed
        return err(-32000, f"internal error: {e}")
