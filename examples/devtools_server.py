"""A coding agent's tool server (the same tool surface Claude Code / Cursor /
Copilot agents use), as a plain MCP server. KEEL's proxy will guard it."""
import pathlib
import subprocess
import sys

from mcp.server.mcpserver import MCPServer

SANDBOX = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/keel-devtest")
SANDBOX.mkdir(parents=True, exist_ok=True)
srv = MCPServer("devtools", instructions="Coding-agent tools: files + shell.")


@srv.tool()
def read_file(path: str) -> str:
    """Read a file in the workspace."""
    return (SANDBOX / path).read_text()[:4000]


@srv.tool()
def write_file(path: str, content: str) -> str:
    """Write a file in the workspace."""
    target = SANDBOX / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return f"wrote {len(content)} bytes to {path}"


@srv.tool()
def run_command(command: str) -> str:
    """Run a shell command in the workspace."""
    r = subprocess.run(command, shell=True, cwd=SANDBOX,
                       capture_output=True, text=True, timeout=20)
    return (r.stdout + r.stderr)[:4000] or f"(exit {r.returncode})"


if __name__ == "__main__":
    srv.run()
