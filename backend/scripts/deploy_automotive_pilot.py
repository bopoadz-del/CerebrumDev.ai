"""Deploy an isolated Automotive Safety Intelligence pilot instance.

Usage:
  python -m scripts.deploy_automotive_pilot --package generated/automotive-safety-intelligence

This script validates the generated package, writes a pilot .env template, and
prints Render/docker-compose deploy instructions. Live Render API create is
opt-in via --apply with RENDER_API_KEY.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib import request


REQUIRED_FILES = (
    "docker-compose.yml",
    "render.yaml",
    ".env.example",
    "app",
    "frontend",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_package(package_dir: Path) -> None:
    missing = [name for name in REQUIRED_FILES if not (package_dir / name).exists()]
    if missing:
        raise SystemExit(f"Generated package missing required entries: {missing}")


def write_pilot_env(package_dir: Path, out_path: Path) -> None:
    content = f"""# Automotive Safety Intelligence pilot environment
# Generated {_utc_now()}

DATABASE_URL=postgresql+psycopg://cerebrum:cerebrum@db:5432/cerebrum
SECRET_KEY=change-me-pilot-secret
BOOTSTRAP_USER_EMAIL=pilot-admin@example.com
BOOTSTRAP_USER_PASSWORD=change-me-pilot-password
RAG_EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
RAG_EMBEDDING_DIMENSIONS=384
RAG_VECTOR_NAMESPACE=v2
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=http://localhost:8000/v1/projects/drive/callback
DATA_ENCRYPTION_KEY=
STORAGE_PATH=/app/storage
"""
    out_path.write_text(content, encoding="utf-8")
    example = package_dir / ".env.example"
    if example.exists():
        # Ensure pilot keys are present in the package template.
        text = example.read_text(encoding="utf-8")
        if "RAG_EMBEDDING_MODEL" not in text:
            example.write_text(text.rstrip() + "\n\n" + content, encoding="utf-8")


def render_create_service(name: str, repo_url: str, branch: str) -> Dict[str, Any]:
    api_key = os.getenv("RENDER_API_KEY")
    if not api_key:
        raise RuntimeError("RENDER_API_KEY is required for --apply")
    payload = {
        "type": "web_service",
        "name": name,
        "repo": repo_url,
        "branch": branch,
        "autoDeploy": "yes",
    }
    req = request.Request(
        "https://api.render.com/v1/services",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Deploy automotive pilot package")
    parser.add_argument(
        "--package",
        type=Path,
        default=Path("generated/automotive-safety-intelligence"),
    )
    parser.add_argument("--apply", action="store_true", help="Call Render API to create service")
    parser.add_argument("--service-name", default="automotive-safety-intelligence-pilot")
    parser.add_argument("--repo-url", default=os.getenv("DEPLOY_REPO_URL", ""))
    parser.add_argument("--branch", default="main")
    args = parser.parse_args(argv)

    package_dir = args.package.resolve()
    validate_package(package_dir)
    write_pilot_env(package_dir, package_dir / ".env.pilot")

    report = {
        "timestamp": _utc_now(),
        "package": str(package_dir),
        "validated": True,
        "env_template": str(package_dir / ".env.pilot"),
        "compose": "docker compose -f docker-compose.yml up --build",
        "health_checks": ["/health", "/ready", "/metrics"],
        "isolation": {
            "database": "dedicated DATABASE_URL",
            "storage": "dedicated STORAGE_PATH / Render disk",
            "secrets": "dedicated SECRET_KEY + DATA_ENCRYPTION_KEY",
            "oauth": "dedicated GOOGLE_* credentials",
            "vector_namespace": "v2 / automotive_core_v1",
        },
    }

    if args.apply:
        if not args.repo_url:
            print("DEPLOY_REPO_URL / --repo-url required with --apply", file=sys.stderr)
            return 1
        created = render_create_service(args.service_name, args.repo_url, args.branch)
        report["render"] = created

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
