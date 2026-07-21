"""Health, readiness, and version probes for Steward pilot certification."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text

from app.steward import STEWARD_RAG_VERSION
from app.steward.config import get_config
from app.steward.db import get_engine, init_engine
from app.steward.embeddings import get_embedder


def _deploy_sha() -> str:
    return (
        os.getenv("STEWARD_DEPLOY_SHA")
        or os.getenv("RENDER_GIT_COMMIT")
        or os.getenv("GIT_COMMIT")
        or "unknown"
    )


def _git_commit_short() -> str:
    sha = _deploy_sha()
    if sha != "unknown":
        return sha[:12]
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        ).strip()
        return out[:12]
    except (subprocess.SubprocessError, FileNotFoundError):
        return "unknown"


def health_payload(*, product_id: str, vertical: str, human_authority: bool) -> Dict[str, Any]:
    return {
        "ok": True,
        "product_id": product_id,
        "vertical": vertical,
        "human_authority": human_authority,
        "steward_rag_version": STEWARD_RAG_VERSION,
    }


def version_payload(*, product_id: str) -> Dict[str, Any]:
    cfg = get_config()
    return {
        "ok": True,
        "product_id": product_id,
        "deploy_sha": _deploy_sha(),
        "deploy_sha_short": _git_commit_short(),
        "steward_rag_version": STEWARD_RAG_VERSION,
        "embed_backend": cfg.embed_backend,
        "legacy_rag_enabled": cfg.legacy_rag_enabled,
        "demo_auth_bypass": cfg.allow_demo_auth_bypass,
        "admin_routes_enabled": cfg.admin_routes_enabled,
    }


def _alembic_head() -> Optional[str]:
    root = Path(__file__).resolve().parent
    cfg = Config()
    cfg.set_main_option("script_location", str(root / "migrations"))
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    return head


def _alembic_current() -> Optional[str]:
    engine = get_engine()
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        return context.get_current_revision()


def readiness_checks() -> Dict[str, Any]:
    cfg = get_config()
    checks: List[Dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    # Auth must be enforced — bypass is a pilot NO-GO
    add(
        "auth_bypass_disabled",
        not cfg.allow_demo_auth_bypass,
        "demo auth bypass must be disabled for pilot readiness",
    )

    # Legacy dual RAG must be off in staging/production profile
    add(
        "canonical_rag_only",
        not cfg.legacy_rag_enabled,
        "legacy /v1/rag/* must be disabled; canonical path is /v1/steward/rag/*",
    )

    # Admin routes must not be public in production profile
    add(
        "admin_routes_disabled",
        not cfg.admin_routes_enabled,
        "public admin migrate/reset routes must be disabled",
    )

    db_ok = False
    alembic_ok = False
    db_detail = "database check not run"
    alembic_detail = "alembic check not run"
    try:
        init_engine()
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
        db_detail = "database connectivity ok"
        head = _alembic_head()
        current = _alembic_current()
        alembic_ok = bool(head) and current == head
        alembic_detail = f"current={current or 'none'} head={head or 'none'}"
    except Exception as exc:
        db_detail = f"database unavailable: {type(exc).__name__}"
        alembic_detail = db_detail

    add("database", db_ok, db_detail)
    add("alembic_at_head", alembic_ok, alembic_detail)

    embed_ok = False
    embed_detail = "embedder check not run"
    try:
        embedder = get_embedder(cfg)
        if cfg.require_production_embeddings:
            embed_ok = embedder.backend_name == "fastembed"
            embed_detail = f"backend={embedder.backend_name} fingerprint={embedder.fingerprint}"
        else:
            embed_ok = True
            embed_detail = f"backend={embedder.backend_name} (production embeddings not required)"
    except Exception as exc:
        embed_detail = f"embedder unavailable: {type(exc).__name__}"

    add("canonical_embedder", embed_ok, embed_detail)

    ready = all(c["ok"] for c in checks)
    return {
        "ok": ready,
        "ready": ready,
        "checks": checks,
        "deploy_sha": _deploy_sha(),
        "steward_rag_version": STEWARD_RAG_VERSION,
    }
