"""KEEL enforcing MCP proxy — the non-bypassable deployment mode.

Sits between any MCP client (Claude Code, Cursor, an agent runtime) and the
real MCP tool servers. The agent connects to KEEL instead of the tools; KEEL
connects to the tools. Every tool call is decided by the gateway BEFORE it is
forwarded — the agent never holds the downstream connection, so there is
nothing to bypass. Action classes are auto-derived from tool schemas: zero
integration code.

    python -m keel.gateway.mcp_proxy --config proxy.json

proxy.json:
    {"agent_id": "cursor-agent",
     "risk_overrides": {"delete_file": "high", "run_command": "high"},
     "servers": [{"name": "fs", "command": "npx",
                  "args": ["-y", "@modelcontextprotocol/server-filesystem", "/work"]}]}

Denied calls return a structured refusal carrying the signed certificate id —
auditable evidence that enforcement happened, per decision, per action.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.server.mcpserver import MCPServer

from .engine import decide, get_agent, register_agent
from .models import ActionClassSpec, ActionRequest, AgentProfile

DEFAULT_RISK = "medium"


async def run_proxy(config: dict[str, Any]) -> None:
    agent_id = config.get("agent_id", "mcp-proxy-agent")
    overrides: dict[str, str] = config.get("risk_overrides", {})
    proxy = MCPServer(
        "keel-guarded-tools",
        instructions="Tools proxied through the KEEL trust gateway. Calls are "
                     "verified before execution; denials carry a signed "
                     "certificate id.")

    async with AsyncExitStack() as stack:
        sessions: dict[str, tuple[ClientSession, str]] = {}
        specs: dict[str, ActionClassSpec] = {}

        for server in config.get("servers", []):
            params = StdioServerParameters(
                command=server["command"], args=server.get("args", []),
                env=server.get("env"))
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            tools = await session.list_tools()
            for tool in tools.tools:
                sessions[tool.name] = (session, server.get("name", "srv"))
                specs[tool.name] = ActionClassSpec(
                    name=tool.name,
                    risk=overrides.get(tool.name, DEFAULT_RISK),  # type: ignore[arg-type]
                    schema=tool.input_schema or None)
                _register_proxied_tool(proxy, tool, sessions, agent_id)

        # the proxy registers the agent with auto-derived action classes.
        # shadow-first by default (observe + sign everything, tripwires still
        # enforce); set "shadow_mode": false in the config to enforce fully
        existing = get_agent(agent_id)
        register_agent(AgentProfile(
            agent_id=agent_id,
            name=existing.name if existing else f"MCP proxy · {agent_id}",
            framework="mcp-proxy",
            shadow_mode=bool(config.get("shadow_mode", True)),
            action_classes={**(existing.action_classes if existing else {}),
                            **specs}))
        sys.stderr.write(f"[keel-proxy] guarding {len(sessions)} tools "
                         f"for agent '{agent_id}'\n")
        await proxy.run_stdio_async()


def _register_proxied_tool(proxy: MCPServer, tool: Any,
                           sessions: dict[str, tuple[ClientSession, str]],
                           agent_id: str) -> None:
    name, desc = tool.name, (tool.description or "")
    schema = tool.input_schema or {}
    params = [k for k in (schema.get("properties") or {}) if k.isidentifier()]

    async def _impl(kwargs: dict[str, Any]) -> str:
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        dec = decide(ActionRequest(
            agent_id=agent_id, action_class=name,
            intent=f"MCP tool call {name}",
            payload=kwargs, reversible=True))
        if dec.decision not in ("ALLOW", "SHADOW"):
            return json.dumps({
                "keel": "DENIED", "decision": dec.decision,
                "certificate": dec.cert_id,
                "reasons": dec.reasons[:3],
                "how_to_proceed": "a human can release this from the KEEL "
                                  "approval queue" if dec.decision == "ESCALATE"
                                  else "action refused by policy"})
        session, _ = sessions[name]
        result = await session.call_tool(name, kwargs)
        text = "\n".join(getattr(c, "text", "") for c in result.content
                          if getattr(c, "text", None))
        from .engine import record_outcome
        from .models import ActionOutcome
        record_outcome(ActionOutcome(
            request_id=dec.request_id, success=not result.is_error,
            detail=text[:200], reported_by="mcp-proxy"))
        return json.dumps({"keel": dec.decision if dec.decision == "SHADOW"
                           else "ALLOWED",
                           "certificate": dec.cert_id, "result": text[:8000]})

    # build a wrapper whose SIGNATURE mirrors the downstream tool's schema so
    # the MCP SDK re-derives the same contract for the guarded tool
    sig = ", ".join(f"{k}=None" for k in params)
    body_kwargs = "{" + ", ".join(f"'{k}': {k}" for k in params) + "}"
    ns: dict[str, Any] = {"_impl": _impl}
    exec(f"async def handler({sig}):\n"
         f"    return await _impl({body_kwargs})", ns)
    handler = ns["handler"]
    handler.__name__ = name
    handler.__doc__ = f"[KEEL-guarded] {desc}"
    proxy.tool(name=name, description=f"[KEEL-guarded] {desc}")(handler)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    with open(args.config) as f:
        config = json.load(f)
    asyncio.run(run_proxy(config))


if __name__ == "__main__":
    main()
