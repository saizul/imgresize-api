"""Command line interface: ``imgresize serve | sign | check-config | prune-cache``."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence

from imgresize_api._version import __version__
from imgresize_api.settings import Settings, SettingsError
from imgresize_api.signing import sign_url


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from imgresize_api.app import create_app

    app = create_app(Settings.from_env())
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        server_header=False,
        proxy_headers=args.proxy_headers,
    )
    return 0


def cmd_sign(args: argparse.Namespace) -> int:
    secret = os.environ.get("IMGRESIZE_SECRET", "")
    if not secret:
        print("error: set IMGRESIZE_SECRET to sign URLs", file=sys.stderr)
        return 2
    print(sign_url(secret.encode("utf-8"), args.url, expires_in=args.expires_in))
    return 0


def cmd_check_config(args: argparse.Namespace) -> int:
    from imgresize_api.processing import avif_available

    settings = Settings.from_env()
    summary = {
        "signing_required": settings.signing_required,
        "allowed_sources": list(settings.allowed_sources) or "any public host",
        "allow_private_networks": settings.allow_private_networks,
        "allowed_ports": list(settings.allowed_ports) or "any",
        "max_source_bytes": settings.max_source_bytes,
        "max_upload_bytes": settings.max_upload_bytes,
        "max_pixels": settings.max_pixels,
        "max_dimension": settings.max_dimension,
        "cache_dir": settings.cache_dir if settings.cache_enabled else None,
        "cache_ttl": settings.cache_ttl,
        "input_formats": list(settings.input_formats),
        "avif_output": settings.enable_avif and avif_available(),
    }
    print(json.dumps(summary, indent=2))
    print("config OK")
    return 0


def cmd_prune_cache(args: argparse.Namespace) -> int:
    from imgresize_api.cache import DiskCache

    settings = Settings.from_env()
    if not settings.cache_enabled:
        print("cache is disabled")
        return 0
    stats = DiskCache(settings.cache_dir, settings.cache_ttl, settings.cache_max_bytes).prune()
    print(json.dumps(stats))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="imgresize", description="Self-hosted image resize and convert service.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the HTTP server")
    serve.add_argument("--host", default=os.environ.get("IMGRESIZE_HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.environ.get("IMGRESIZE_PORT", "8080")))
    serve.add_argument(
        "--log-level",
        default=os.environ.get("IMGRESIZE_LOG_LEVEL", "info"),
        choices=["debug", "info", "warning", "error"],
    )
    serve.add_argument("--proxy-headers", action="store_true", help="trust X-Forwarded-* from the reverse proxy")
    serve.set_defaults(func=cmd_serve)

    sign = sub.add_parser("sign", help="sign a /resize URL with IMGRESIZE_SECRET")
    sign.add_argument("url", help="for example '/resize?url=https://example.com/a.jpg&w=400&format=webp'")
    sign.add_argument("--expires-in", type=int, default=None, metavar="SECONDS", help="add an expiry")
    sign.set_defaults(func=cmd_sign)

    check = sub.add_parser("check-config", help="validate the IMGRESIZE_* environment and print a summary")
    check.set_defaults(func=cmd_check_config)

    prune = sub.add_parser("prune-cache", help="delete expired cache entries and enforce the size cap")
    prune.set_defaults(func=cmd_prune_cache)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(args, "log_level", "info").upper(), format="%(asctime)s %(levelname)s %(message)s"
    )
    try:
        return int(args.func(args))
    except SettingsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
