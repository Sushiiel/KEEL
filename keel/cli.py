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


def _verify(args: argparse.Namespace) -> None:
    """Verify a certificate offline: signature + Merkle inclusion.

    Needs only the JSON and the authority's public key — no server, no
    account, no network. This is the command you hand an auditor.
    """
    import json as _json

    from .cert import verifier

    with open(args.certificate) as f:
        bundle = _json.load(f)

    key = (args.key or os.environ.get("KEEL_PUBLIC_KEY", "")).strip()
    key_source = "--key" if args.key else "KEEL_PUBLIC_KEY"
    if not key:
        # last resort: a key embedded in the bundle itself (audit packs carry
        # one). Verifying against it proves internal consistency only — the
        # document vouching for itself — so say so loudly.
        key = str(bundle.get("authority_public_key", "")).strip()
        key_source = "embedded in the bundle (SELF-REFERENTIAL)"
    if not key:
        print("error: no public key. Pass --key <hex>, set KEEL_PUBLIC_KEY, or "
              "verify a bundle that embeds authority_public_key.", file=sys.stderr)
        sys.exit(2)

    report = verifier.verify(bundle, key, expected_root=args.root)
    if args.json:
        print(_json.dumps(report, indent=2))
    else:
        print(f"certificate : {report.get('cert_id') or '(unknown)'}")
        print(f"signer      : {report.get('signer') or '(unknown)'}")
        print(f"public key  : {key[:16]}…  [{key_source}]")
        for name, ok in report.get("checks", {}).items():
            mark = "✓" if ok else ("—" if ok is None else "✗")
            note = "" if ok is not None else "  (not supplied — not checked)"
            print(f"  {mark} {name}{note}")
        if "log" in report:
            lg = report["log"]
            print(f"log         : entry {lg.get('index')} of {lg.get('size')}, "
                  f"root {str(lg.get('root'))[:16]}…")
        if "SELF-REFERENTIAL" in key_source and report["valid"]:
            print("note        : verified against a key embedded in the same "
                  "document. Obtain the key out-of-band to rule out a swapped "
                  "bundle.")
        print("result      : " + ("VALID" if report["valid"] else "NOT VALID"))
    sys.exit(0 if report["valid"] else 1)


def _passport_verify(args: argparse.Namespace) -> None:
    """Verify an agent passport offline — the receiving side's due diligence."""
    import json as _json

    from .gateway.passport import verify_passport
    with open(args.passport) as f:
        passport = _json.load(f)
    report = verify_passport(passport, args.key or "")
    print(f"agent       : {report.get('agent_id') or '(unknown)'}")
    for name, ok in report.get("checks", {}).items():
        print(f"  {'✓' if ok else '✗'} {name}")
    if not report.get("key_pinned") and report.get("valid"):
        # internally consistent, but the document is vouching for itself —
        # an attacker can mint one with any key. Never exit 0 for that: this
        # command's exit code will end up inside someone's automation, and an
        # automated gate that passes self-signed trust is worse than none.
        print("result      : UNPINNED — internally consistent, but verified "
              "against the key embedded in the passport itself. Anyone can "
              "mint such a document. Pass --key with the issuer's key obtained "
              "out-of-band; refusing to treat this as success.")
        sys.exit(1)
    print("result      : " + ("VALID" if report.get("valid") else "NOT VALID"))
    sys.exit(0 if report.get("valid") else 1)


def _checkpoint_compare(args: argparse.Namespace) -> None:
    """Compare two signed log checkpoints; detect forks and truncation."""
    import json as _json

    from .cert.verifier import compare_checkpoints
    with open(args.older) as f:
        older = _json.load(f)
    with open(args.newer) as f:
        newer = _json.load(f)
    key = (args.key or os.environ.get("KEEL_PUBLIC_KEY", "")
           or str(older.get("public_key", ""))).strip()
    report = compare_checkpoints(older, newer, key)
    print(f"older       : size {older.get('size')}  root {str(older.get('root'))[:16]}…")
    print(f"newer       : size {newer.get('size')}  root {str(newer.get('root'))[:16]}…")
    print(f"verdict     : {report.get('verdict')}")
    print(f"detail      : {report.get('detail')}")
    # only proven misbehaviour and unverifiable input are failures; growth is
    # the normal, healthy case
    sys.exit(0 if report.get("verdict") in ("IDENTICAL", "APPEND-CONSISTENT")
             else 1)


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

    v = sub.add_parser("verify",
                       help="verify a certificate offline (signature + Merkle inclusion)")
    v.add_argument("certificate", help="path to a certificate JSON (bare, "
                   "/api/certificates response, or audit-pack sample)")
    v.add_argument("--key", default="", help="authority public key, hex "
                   "(or set KEEL_PUBLIC_KEY)")
    v.add_argument("--root", default=None,
                   help="pin the expected transparency-log root (hex)")
    v.add_argument("--json", action="store_true", help="machine-readable report")
    v.set_defaults(func=_verify)

    pv = sub.add_parser("passport",
                        help="agent passports: portable, verifiable trust records")
    psub = pv.add_subparsers(dest="passport_cmd")
    pvv = psub.add_parser("verify", help="verify a passport offline")
    pvv.add_argument("passport", help="path to a passport JSON")
    pvv.add_argument("--key", default="",
                     help="issuer public key hex, obtained out-of-band")
    pvv.set_defaults(func=_passport_verify)

    cp = sub.add_parser("checkpoint",
                        help="signed transparency-log checkpoints")
    csub = cp.add_subparsers(dest="checkpoint_cmd")
    cpc = csub.add_parser("compare",
                          help="compare two checkpoints; detect forks/truncation")
    cpc.add_argument("older", help="earlier checkpoint JSON")
    cpc.add_argument("newer", help="later checkpoint JSON")
    cpc.add_argument("--key", default="", help="authority public key hex")
    cpc.set_defaults(func=_checkpoint_compare)

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
