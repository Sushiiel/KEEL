"""Drive a coding agent's session through the KEEL-guarded toolchain.

This client speaks MCP exactly like Claude Code / Cursor do. It connects to
the KEEL proxy (which holds the real tools) and plays a realistic session —
including the catastrophe every company fears from this product category.
"""
import asyncio
import json
import sys
import urllib.request

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

PROXY = StdioServerParameters(
    command=sys.executable.replace("python3", "python") if False else sys.executable,
    args=["-m", "keel.gateway.mcp_proxy", "--config",
          "examples/coding_agent_proxy.json"])


async def call(session, tool, **kwargs):
    res = await session.call_tool(tool, kwargs)
    text = "".join(getattr(c, "text", "") for c in res.content)
    try:
        body = json.loads(text)
    except Exception:
        body = {"raw": text[:200]}
    verdict = body.get("keel", "?")
    extra = (body.get("result", "") or " ".join(body.get("reasons", [])))[:90]
    print(f"  {tool:<12} → {verdict:<8} {extra}")
    return body


async def main():
    async with stdio_client(PROXY) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f"[1] connected through KEEL — guarded tools: "
                  f"{[t.name for t in tools.tools]}\n")
            print("[2] a normal coding session (shadow mode — signed, not blocked):")
            await call(session, "read_file", path="app.py")
            await call(session, "write_file", path="feature.py",
                       content="def feature():\n    return 'shipped'\n")
            await call(session, "run_command", command="ls -la")
            print("\n[3] the Replit/Gemini-class catastrophe attempt:")
            body = await call(session, "run_command",
                              command="rm -rf /tmp/keel-devtest && echo clean")
            assert body.get("keel") == "DENIED", "tripwire must fire"
            print(f"      certificate of the denial: {body['certificate']}")
            print("\n[4] destructive SQL through the same tool:")
            await call(session, "run_command",
                       command="psql -c 'DROP TABLE users;'")

    led = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:8347/api/translog?domain=gateway").read())
    print(f"\n[5] gateway ledger: {led['chain']['size']} signed decisions · "
          f"chain consistent: {led['chain']['consistent']}")
    print("Open the site → Agent Gateway: 'claude-code' is now a registered "
          "agent with live decisions.")

asyncio.run(main())
