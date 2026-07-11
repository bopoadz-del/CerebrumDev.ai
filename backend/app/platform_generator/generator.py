"""Core platform generator: copies The_Fork baseline and applies transformations."""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.models.platform_manifest import (
    AutomotivePlatformManifest,
    parse_manifest,
    validate_manifest,
)
from app.platform_generator.fork_baseline import FORK_BASELINE_COMMIT, FORK_SOURCE_DIRS
from app.platform_generator.renderers import (
    copy_and_transform,
    render_manifest_json,
    write_inputs_hash,
)
from app.platform_generator.template_filters import make_text_filter

logger = logging.getLogger(__name__)

DEFAULT_FORK_PATH_ENV = "FORK_BASELINE_PATH"
DEFAULT_BLOCKS_PATH_ENV = "CEREBRUM_BLOCKS_PATH"


@dataclass(frozen=True)
class GenerationInputsHash:
    """Value object returned by the generator."""

    hash: str
    fork_commit: str
    blocks_commit: str


class PlatformGenerator:
    """Generate a domain-branded platform package from The_Fork baseline.

    Usage:
        manifest = parse_manifest({...})
        gen = PlatformGenerator(manifest)
        inputs_hash = gen.generate(Path("generated/automotive"))
    """

    def __init__(
        self,
        manifest: AutomotivePlatformManifest,
        fork_path: Path | str | None = None,
        blocks_path: Path | str | None = None,
    ):
        validate_manifest(manifest)
        self.manifest = manifest
        self.fork_path = Path(
            fork_path or os.getenv(DEFAULT_FORK_PATH_ENV, "../The_Fork")
        ).resolve()
        self.blocks_path = Path(
            blocks_path or os.getenv(DEFAULT_BLOCKS_PATH_ENV, "../Cerebrum-Blocks")
        ).resolve()

    def _verify_fork_baseline(self) -> str:
        """Confirm the fork path points to the pinned commit.

        Returns the resolved commit SHA. Raises RuntimeError on mismatch.
        """
        if not self.fork_path.exists():
            raise RuntimeError(f"The_Fork path does not exist: {self.fork_path}")

        git_dir = self.fork_path / ".git"
        if not git_dir.exists():
            # Without a git checkout we can't verify; warn and proceed.
            logger.warning(
                "The_Fork path is not a git checkout; cannot verify commit pin"
            )
            return "unknown"

        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.fork_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Could not read The_Fork HEAD: {result.stderr.strip()}"
            )
        head = result.stdout.strip()
        if head != FORK_BASELINE_COMMIT:
            raise RuntimeError(
                f"The_Fork HEAD {head} does not match pinned baseline "
                f"{FORK_BASELINE_COMMIT}. Update the pin or path."
            )
        return head

    def _verify_fork_layout(self) -> None:
        """Check that the expected top-level files/directories are present."""
        missing = FORK_SOURCE_DIRS - {p.name for p in self.fork_path.iterdir()}
        if missing:
            raise RuntimeError(
                f"The_Fork baseline is missing expected entries: {sorted(missing)}"
            )

    def _blocks_commit(self) -> str:
        """Return the current Cerebrum-Blocks commit, or 'unknown'."""
        git_dir = self.blocks_path / ".git"
        if not git_dir.exists():
            return "unknown"
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.blocks_path,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return "unknown"
        return result.stdout.strip()

    def generate(
        self,
        output_dir: Path | str,
        extra_replacements: dict[str, str] | None = None,
    ) -> GenerationInputsHash:
        """Generate the platform package.

        Steps:
        1. Verify The_Fork baseline.
        2. Copy The_Fork tree into ``output_dir``.
        3. Apply text substitutions.
        4. Write platform_manifest.json and inputs hash.
        5. Sanitize package of secrets/live data.
        """
        output = Path(output_dir)
        if output.exists():
            raise RuntimeError(
                f"Output directory already exists: {output}. "
                "Delete it or choose a different path."
            )

        fork_commit = self._verify_fork_baseline()
        self._verify_fork_layout()
        blocks_commit = self._blocks_commit()

        logger.info(
            "Generating %s from The_Fork@%s and Cerebrum-Blocks@%s into %s",
            self.manifest.platform_id,
            fork_commit,
            blocks_commit,
            output,
        )

        text_filter = make_text_filter(self.manifest, extra_replacements)
        copy_and_transform(self.fork_path, output, text_filter, sanitize=True)
        render_manifest_json(output, self.manifest)
        write_inputs_hash(output, fork_commit, blocks_commit, self.manifest)

        logger.info("Generated platform package at %s", output)
        return GenerationInputsHash(
            hash=self._read_hash(output),
            fork_commit=fork_commit,
            blocks_commit=blocks_commit,
        )

    @staticmethod
    def _read_hash(output: Path) -> str:
        hash_file = output / ".generation_inputs_hash"
        first_line = hash_file.read_text(encoding="utf-8").splitlines()[0]
        return first_line.strip()

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        fork_path: Path | str | None = None,
        blocks_path: Path | str | None = None,
    ) -> "PlatformGenerator":
        """Convenience constructor from a raw manifest dict."""
        manifest = parse_manifest(data)
        return cls(manifest, fork_path=fork_path, blocks_path=blocks_path)
