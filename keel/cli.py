"""KEEL command-line interface.

    keel serve            # start the gateway + console + site (default :8347)
    keel serve --port 9000 --sandbox
    keel version
    keel guard <config>   # run the enforcing MCP proxy from a config file
"""
from __future__ import annotations

import argparse
import os
import sys

from . import __version__


def _serve(args: argparse.Namespace) -> None:
    if args.sandbox:
        os.environ["KEEL_SANDBOX"] = "1"
    if args.data_dir:
        os.environ["KEEL_DATA_DIR"] = args.data_dir
    import uvicorn
    print(f"KEEL {__version__} — runtime trust layer for agentic AI")
    print(f"  console : http://{args.host}:{args.port}/app")
    print(f"  docs    : http://{args.host}:{args.port}/docs")
    print(f"  gateway : POST http://{args.host}:{args.port}/api/gateway/check")
    if os.environ.get("KEEL_SANDBOX") == "1":
        print("  sandbox demo worlds: ENABLED")
    uvicorn.run("keel.server.app:app", host=args.host, port=args.port,
                log_level=args.log_level)


def _guard(args: argparse.Namespace) -> None:
    import asyncio
    import json

    from .gateway.mcp_proxy import run_proxy
    with open(args.config) as f:
        asyncio.run(run_proxy(json.load(f)))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="keel",
                                description="Runtime trust layer for agentic AI")
    p.add_argument("--version", action="version", version=f"keel {__version__}")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("serve", help="start the gateway, console, and site")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=int(os.environ.get("PORT", os.environ.get("KEEL_PORT", 8347))))
    s.add_argument("--sandbox", action="store_true",
                   help="enable the simulated demo worlds (evaluation only)")
    s.add_argument("--data-dir", default=None, help="where to store data")
    s.add_argument("--log-level", default="warning")
    s.set_defaults(func=_serve)

    g = sub.add_parser("guard", help="run the enforcing MCP proxy")
    g.add_argument("config", help="path to a proxy config JSON file")
    g.set_defaults(func=_guard)

    sub.add_parser("version", help="print version").set_defaults(
        func=lambda a: print(f"keel {__version__}"))

    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        p.print_help()
        return 1
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
