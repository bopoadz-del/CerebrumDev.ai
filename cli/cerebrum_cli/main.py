"""Entry point for the ``cerebrum`` CLI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx

from .auth import bearer_header, resolve_auth
from .config import load_config, mask, save_config
from .sse import stream_turn


def _client() -> httpx.Client:
    return httpx.Client()


def _session_id(args: argparse.Namespace, cfg: dict[str, Any]) -> str:
    sid = args.session or cfg.get("session_id", "")
    if not sid:
        sys.exit("No session_id. Use --session or set session_id in ~/.cerebrum/config.toml.")
    return sid


def cmd_init(args: argparse.Namespace, cfg: dict[str, Any]) -> None:
    """Interactively write ~/.cerebrum/config.toml."""
    print("Cerebrum CLI init")
    base_url = input(f"Base URL [{cfg['base_url']}]: ").strip() or cfg["base_url"]
    api_key = input("API key: ").strip()
    domain = input(f"Domain [{cfg['domain']}]: ").strip() or cfg["domain"]
    instance_name = (
        input(f"Instance name [{cfg['instance_name']}]: ").strip() or cfg["instance_name"]
    )
    session_id = input("Default session ID (optional): ").strip()
    save_config(
        {
            "base_url": base_url,
            "api_key": api_key,
            "domain": domain,
            "instance_name": instance_name,
            "session_id": session_id,
        }
    )
    print(f"Wrote {Path.home() / '.cerebrum' / 'config.toml'}")


def cmd_config_show(args: argparse.Namespace, cfg: dict[str, Any]) -> None:
    """Print resolved config with api_key masked."""
    display = cfg.copy()
    display["api_key"] = mask(display.get("api_key"))
    print(json.dumps(display, indent=2))


def cmd_health(args: argparse.Namespace, cfg: dict[str, Any]) -> None:
    cred = resolve_auth(
        cfg["base_url"],
        token=args.token,
        api_key=args.api_key,
        email=args.email,
        password=args.password,
    )
    with _client() as client:
        r = client.get(f"{cfg['base_url']}/health", headers=bearer_header(cred), timeout=30)
    print(f"HTTP {r.status_code}")
    try:
        print(json.dumps(r.json(), indent=2)[:8000])
    except Exception:
        print(r.text[:2000])


def _not_available(name: str) -> None:
    print(f"# TODO: deployed-instance endpoint — Spec 2")
    print(f"'{name}' is not available on this instance type.")


def cmd_chat(args: argparse.Namespace, cfg: dict[str, Any]) -> None:
    cred = resolve_auth(
        cfg["base_url"],
        token=args.token,
        api_key=args.api_key,
        email=args.email,
        password=args.password,
    )
    sid = _session_id(args, cfg)
    url = f"{cfg['base_url']}/v1/sessions/{sid}/chat"
    headers = bearer_header(cred)

    with _client() as client:
        if args.message:
            body: dict[str, Any] = {"message": args.message}
            stream_turn(
                client,
                url,
                body,
                headers,
                raw=args.raw,
                events=args.events,
            )
        if args.repl:
            print("REPL — Ctrl-D or 'exit' to quit.")
            while True:
                try:
                    msg = input("\ncerebrum> ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if not msg or msg.lower() in ("exit", "quit"):
                    break
                stream_turn(
                    client,
                    url,
                    {"message": msg},
                    headers,
                    raw=args.raw,
                    events=args.events,
                )


def cmd_upload(args: argparse.Namespace, cfg: dict[str, Any]) -> None:
    cred = resolve_auth(
        cfg["base_url"],
        token=args.token,
        api_key=args.api_key,
        email=args.email,
        password=args.password,
    )
    sid = _session_id(args, cfg)
    url = f"{cfg['base_url']}/v1/sessions/{sid}/upload"
    headers = bearer_header(cred)

    files: dict[str, Any] = {}
    for path in args.files:
        p = Path(path)
        if not p.exists():
            sys.exit(f"File not found: {path}")
        files["files"] = (p.name, p.read_bytes())

    with _client() as client:
        r = client.post(url, files=files, headers=headers, timeout=120)
    print(f"HTTP {r.status_code}")
    try:
        print(json.dumps(r.json(), indent=2)[:8000])
    except Exception:
        print(r.text[:2000])


def cmd_reindex(args: argparse.Namespace, cfg: dict[str, Any]) -> None:
    _not_available("reindex")


def cmd_chain_show(args: argparse.Namespace, cfg: dict[str, Any]) -> None:
    cred = resolve_auth(
        cfg["base_url"],
        token=args.token,
        api_key=args.api_key,
        email=args.email,
        password=args.password,
    )
    sid = _session_id(args, cfg)
    url = f"{cfg['base_url']}/v1/sessions/{sid}/chain/preview"
    with _client() as client:
        r = client.get(url, headers=bearer_header(cred), timeout=30)
    print(f"HTTP {r.status_code}")
    try:
        print(json.dumps(r.json(), indent=2)[:8000])
    except Exception:
        print(r.text[:2000])


def cmd_rules_list(args: argparse.Namespace, cfg: dict[str, Any]) -> None:
    _not_available("rules list")


def cmd_deploy_status(args: argparse.Namespace, cfg: dict[str, Any]) -> None:
    cred = resolve_auth(
        cfg["base_url"],
        token=args.token,
        api_key=args.api_key,
        email=args.email,
        password=args.password,
    )
    sid = _session_id(args, cfg)
    url = f"{cfg['base_url']}/v1/sessions/{sid}/deploy/status"
    with _client() as client:
        r = client.get(url, headers=bearer_header(cred), timeout=30)
    print(f"HTTP {r.status_code}")
    try:
        print(json.dumps(r.json(), indent=2)[:8000])
    except Exception:
        print(r.text[:2000])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cerebrum", description="Terminal client for CerebrumDev.ai")
    p.add_argument("--base-url", help="API base URL")
    p.add_argument("--api-key", help="API key")
    p.add_argument("--token", help="JWT token")
    p.add_argument("--email", help="Login email")
    p.add_argument("--password", help="Login password")
    p.add_argument("--session", help="Session ID")
    p.add_argument(
        "--pack",
        action="store_true",
        help="Placeholder flag for future packaging integration. (TODO)",
    )

    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="interactively create ~/.cerebrum/config.toml")
    sub.add_parser("config", help="show resolved config")

    h = sub.add_parser("health", help="GET /health")
    h.add_argument("--domain", help="Domain override")

    c = sub.add_parser("chat", help="chat with full SSE event + timing dump")
    c.add_argument("message", nargs="?", help="one-shot message (omit with --repl)")
    c.add_argument("--events", action="store_true", help="event-trace mode")
    c.add_argument("--raw", action="store_true", help="dump raw SSE data lines")
    c.add_argument("--repl", action="store_true", help="interactive loop")

    u = sub.add_parser("upload", help="upload files to a session")
    u.add_argument("files", nargs="+", help="files to upload")

    sub.add_parser("reindex", help="trigger reindexing (deployed-instance endpoint — Spec 2)")

    cs = sub.add_parser("chain", help="show proposed chain")
    cs.add_argument("subcmd", choices=["show"], default="show", nargs="?")

    r = sub.add_parser("rules", help="list extracted rules")
    r.add_argument("subcmd", choices=["list"], default="list", nargs="?")

    d = sub.add_parser("deploy", help="deployment status")
    d.add_argument("subcmd", choices=["status"], default="status", nargs="?")

    return p


def main(argv: list[str] | None = None) -> int:
    global cfg
    parser = build_parser()
    args = parser.parse_args(argv)

    cfg = load_config(
        base_url=args.base_url,
        api_key=args.api_key,
        session_id=args.session,
    )

    if args.cmd == "init":
        cmd_init(args, cfg)
    elif args.cmd == "config":
        cmd_config_show(args, cfg)
    elif args.cmd == "health":
        cmd_health(args, cfg)
    elif args.cmd == "chat":
        if not args.message and not args.repl:
            parser.error("chat needs a message or --repl")
        cmd_chat(args, cfg)
    elif args.cmd == "upload":
        cmd_upload(args, cfg)
    elif args.cmd == "reindex":
        cmd_reindex(args, cfg)
    elif args.cmd == "chain":
        cmd_chain_show(args, cfg)
    elif args.cmd == "rules":
        cmd_rules_list(args, cfg)
    elif args.cmd == "deploy":
        cmd_deploy_status(args, cfg)
    else:
        parser.error(f"unknown command: {args.cmd}")

    return 0


# Loaded once for cmd_init convenience.
cfg = load_config()
