"""Render helpers: file copying, text substitution, and package sanitization."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path
from typing import Callable

from app.models.platform_manifest import AutomotivePlatformManifest
from app.platform_generator.template_filters import make_text_filter, should_transform_file


def copy_and_transform(
    src: Path,
    dst: Path,
    text_filter: Callable[[str], str],
    sanitize: bool = True,
) -> None:
    """Copy ``src`` to ``dst``, applying ``text_filter`` to text files.

    Directories are created as needed. Existing files are overwritten.
    """
    if src.is_dir():
        dst.mkdir(parents=True, exist_ok=True)
        for child in sorted(src.iterdir()):
            copy_and_transform(child, dst / child.name, text_filter, sanitize=sanitize)
        return

    dst.parent.mkdir(parents=True, exist_ok=True)

    if sanitize and _should_skip_file(src.name):
        return

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
    skip = {
        ".env", ".env.local", ".env.production", ".env.development",
        ".secret_key", "secret_key", "service-account.json",
    }
    return lower in skip or lower.endswith(".secret")


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
