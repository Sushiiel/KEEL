"""KEEL MCP server — the causal engine as a tool surface for any MCP client.

Claude Code, Cursor, and internal copilots can query KEEL directly: list
incidents, submit a causal claim for adjudication, fetch and verify signed
certificates. Runs over stdio with the official MCP Python SDK:

    .venv/bin/python -m keel.interop.mcp_server

Claude Code registration:
    claude mcp add keel -- /path/to/keel/.venv/bin/python -m keel.interop.mcp_server

The MCP surface goes through exactly the same pipeline as the UI and A2A —
one engine, three doors, identical guarantees.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from mcp.server.mcpserver import MCPServer

from ..cert import authority, translog
from ..domains import all_packs
from ..pipeline import Runtime, boot, execute_certificate, run_verification

mcp = MCPServer("keel", instructions=(
    "KEEL is a causal verification authority. It adjudicates root-cause claims "
    "about infrastructure incidents against a learned structural causal model "
    "and returns signed, conformally-calibrated Causal Certificates. Use "
    "verify_incident to adjudicate; the verdict, PN interval, refutations, and "
    "signature are all on the certificate. ABSTAIN is a first-class answer."))

_runtimes: dict[str, Runtime] = {}


def _rt(domain: str) -> Runtime:
    if domain not in _runtimes:
        _runtimes[domain] = boot(domain)
    return _runtimes[domain]


@mcp.tool()
def list_domains() -> str:
    """List the available domain workspaces (industries) and their tenants."""
    return json.dumps([{"key": k, "name": p.name, "tenant": p.tenant}
                       for k, p in sorted(all_packs().items())])


@mcp.tool()
def list_incidents(domain: str = "telecom", limit: int = 20) -> str:
    """List recent incidents in a domain: id, title, status, alarm count."""
    rt = _rt(domain)
    return json.dumps([
        {"incident_id": i.incident_id, "title": i.title, "status": i.status,
         "severity": i.severity, "alarms": i.alarm_count}
        for i in rt.store.incidents(limit=limit)])


@mcp.tool()
def verify_incident(incident_id: str, domain: str = "telecom",
                    claim_variable: Optional[str] = None,
                    claimant: str = "mcp-client") -> str:
    """Run causal verification for an incident. Optionally pass an external
    claim as '<entity_id>|<event_type>' to adjudicate it specifically.
    Returns the signed Causal Certificate (verdict, PN/PS intervals,
    refutations, conformal set, action gate decision)."""
    rt = _rt(domain)
    cert = None
    for step in run_verification(rt, incident_id, claim_variable=claim_variable,
                                 claimant=claimant):
        if step["stage"] == "certificate":
            cert = step["data"]["certificate"]
        if step["stage"] == "error":
            return json.dumps({"error": step["detail"]})
    return json.dumps(cert or {"error": "no certificate produced"})


@mcp.tool()
def get_certificate(cert_id: str, domain: str = "telecom") -> str:
    """Fetch a certificate with its Ed25519 verification result and Merkle
    inclusion proof from the transparency log."""
    rt = _rt(domain)
    cert = rt.store.certificate(cert_id)
    if cert is None:
        return json.dumps({"error": "unknown certificate"})
    proof = (translog.inclusion_proof(rt.store, cert.log_index)
             if cert.log_index is not None else None)
    return json.dumps({"certificate": cert.model_dump(),
                       "verification": authority.verify(cert),
                       "inclusion_proof": proof})


@mcp.tool()
def execute_remediation(cert_id: str, domain: str = "telecom",
                        approver: str = "mcp-client") -> str:
    """Execute the remediation on a SUPPORTED certificate through the CMDP
    shield and policy gate. Refused if the gate blocked the action."""
    return json.dumps(execute_certificate(_rt(domain), cert_id, approver=approver))


if __name__ == "__main__":
    mcp.run()
