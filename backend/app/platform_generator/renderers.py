"""Render helpers: file copying, text substitution, and package sanitization."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Callable

from app.models.platform_manifest import AutomotivePlatformManifest
from app.platform_generator.template_filters import should_transform_file


# Directory names that must not be copied into a generated package.
_SKIP_DIR_NAMES: set[str] = {
    ".git",
    ".github",
    ".claude",
    ".ci-investigation",
    ".vscode",
    ".idea",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".venv",
    ".tox",
    "data",
    "logs",
    "uploads",
    "tmp",
    "temp",
    "review_pack",
}

# File name prefixes that indicate runtime data or live corpus artifacts.
_SKIP_FILE_PREFIXES: set[str] = {
    "rag_backfill_",
    "feature_matrix_results",
    "golden_set_results",
    "bulk_render_",
    "migrate_drive_archive",
    "remaining_RAG_to_ingest",
    "rag_render_bulk_ingest_checkpoint",
}

# File suffixes that are logs, archives, line-delimited dumps, or temp artifacts.
_SKIP_FILE_SUFFIXES: set[str] = {
    ".log",
    ".zip",
    ".jsonl",
    ".secret",
}


def copy_and_transform(
    src: Path,
    dst: Path,
    text_filter: Callable[[str], str],
    sanitize: bool = True,
    src_root: Path | None = None,
) -> None:
    """Copy ``src`` to ``dst``, applying ``text_filter`` to text files.

    Directories are created as needed. Existing files are overwritten.
    Runtime data, VCS metadata, and live corpus artifacts are skipped when
    ``sanitize`` is True.
    """
    if src_root is None:
        src_root = src

    rel = src.relative_to(src_root)

    if src.is_dir():
        if sanitize and any(part in _SKIP_DIR_NAMES for part in rel.parts):
            return
        dst.mkdir(parents=True, exist_ok=True)
        for child in sorted(src.iterdir()):
            copy_and_transform(
                child,
                dst / child.name,
                text_filter,
                sanitize=sanitize,
                src_root=src_root,
            )
        return

    if sanitize and _should_skip_file(src.name):
        return

    dst.parent.mkdir(parents=True, exist_ok=True)

    if should_transform_file(str(src)):
        text = src.read_text(encoding="utf-8", errors="surrogateescape")
        transformed = text_filter(text)
        with dst.open("w", encoding="utf-8", errors="surrogateescape") as f:
            f.write(transformed)
    else:
        shutil.copy2(src, dst)


def _should_skip_file(name: str) -> bool:
    """Files that must never be copied into a generated package."""
    lower = name.lower()
    skip_names = {
        ".env",
        ".env.local",
        ".env.production",
        ".env.development",
        ".secret_key",
        "secret_key",
        "service-account.json",
    }
    if lower in skip_names or lower.endswith(".secret"):
        return True
    if any(lower.startswith(prefix) for prefix in _SKIP_FILE_PREFIXES):
        return True
    if any(lower.endswith(suffix) for suffix in _SKIP_FILE_SUFFIXES):
        return True
    return False


def write_inputs_hash(
    output_dir: Path,
    fork_commit: str,
    blocks_commit: str,
    manifest: AutomotivePlatformManifest,
) -> None:
    """Write a deterministic hash of the generation inputs.

    The hash lets callers detect when a regenerated package was produced from
    the same inputs.
    """
    payload = "|".join([
        fork_commit,
        blocks_commit,
        manifest.model_dump_json(),
    ]).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    hash_file = output_dir / ".generation_inputs_hash"
    hash_file.write_text(
        f"{digest}\nfork_commit={fork_commit}\nblocks_commit={blocks_commit}\n",
        encoding="utf-8",
    )


def render_manifest_json(
    output_dir: Path,
    manifest: AutomotivePlatformManifest,
) -> None:
    """Write the resolved platform manifest into the generated package."""
    manifest_path = output_dir / "platform_manifest.json"
    manifest_path.write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )
