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


def _keygen(args: argparse.Namespace) -> None:
    """Print a fresh authority signing key for KEEL_SIGNING_KEY_PEM."""
    from .cert.authority import export_private_pem
    pem = export_private_pem()
    if args.quiet:
        print(pem, end="")
        return
    print("KEEL authority signing key — Ed25519, PKCS#8 PEM.\n")
    print(pem, end="")
    print("\nSet this as KEEL_SIGNING_KEY_PEM in your host's environment "
          "(Render/Fly/Heroku dashboard, or a secret manager).\n"
          "Why it matters: this key signs every certificate, the transparency\n"
          "root, and every licence. On an ephemeral filesystem a file-backed\n"
          "key is regenerated on each deploy, which invalidates all of them.\n\n"
          "Treat it like a private key: never commit it, never paste it into\n"
          "chat or a ticket. To rotate, replace the value — previously issued\n"
          "certificates then verify against the retired public key only.")


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

    k = sub.add_parser("keygen",
                       help="generate the authority signing key (KEEL_SIGNING_KEY_PEM)")
    k.add_argument("--quiet", action="store_true", help="print only the PEM")
    k.set_defaults(func=_keygen)

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
