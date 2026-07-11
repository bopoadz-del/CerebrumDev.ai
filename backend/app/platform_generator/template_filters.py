"""Text and path filters for transforming The_Fork into a domain platform."""

from __future__ import annotations

import re
from typing import Callable

from app.models.platform_manifest import AutomotivePlatformManifest


# Default string mapping from construction concepts to automotive concepts.
# Keys are literal strings; values are Jinja-like placeholders or literal
# replacements. The renderer substitutes placeholders using manifest values.
DEFAULT_CONSTRUCTION_TO_DOMAIN: dict[str, str] = {
    "The Fork": "{{ product_name }}",
    "Cerebrum Blocks": "{{ product_name }}",
    "Construction Workspace": "{{ workspace_name }}",
    "Vehicle Safety Workspace": "{{ workspace_name }}",  # idempotent
    "training_material": "automotive_core_knowledge",
    "Training material": "Automotive core knowledge",
    "RFI": "recall",
    "NCR": "complaint",
    "VO": "investigation",
    "BOQ": "safety_rating",
    "Dar Al Arkan Master Corpus": "{{ foundation_name }}",
    "master corpus": "foundation corpus",
    "Master Corpus": "Foundation Corpus",
    "construction project": "automotive project",
    "construction projects": "automotive projects",
    "Construction project": "Automotive project",
    "Construction projects": "Automotive projects",
}


def _build_replacements(
    manifest: AutomotivePlatformManifest,
    extra: dict[str, str] | None = None,
) -> list[tuple[re.Pattern, str]]:
    """Compile ordered (pattern, replacement) pairs from the mapping."""
    mapping = dict(DEFAULT_CONSTRUCTION_TO_DOMAIN)
    if extra:
        mapping.update(extra)

    # Resolve manifest placeholders.
    resolved: dict[str, str] = {}
    for key, value in mapping.items():
        resolved[key] = (
            value.replace("{{ product_name }}", manifest.branding.product_name)
            .replace("{{ workspace_name }}", manifest.branding.workspace_name)
            .replace("{{ foundation_name }}", manifest.branding.foundation_name)
        )

    # Sort by length descending so longer phrases replace before shorter ones.
    ordered = sorted(resolved.items(), key=lambda item: len(item[0]), reverse=True)
    return [(re.compile(re.escape(k)), v) for k, v in ordered]


def make_text_filter(
    manifest: AutomotivePlatformManifest,
    extra: dict[str, str] | None = None,
) -> Callable[[str], str]:
    """Return a callable that applies domain replacements to a text string."""
    replacements = _build_replacements(manifest, extra)

    def _filter(text: str) -> str:
        for pattern, replacement in replacements:
            text = pattern.sub(replacement, text)
        return text

    return _filter


def should_transform_file(path: str) -> bool:
    """Return True if the file at ``path`` should receive text substitutions.

    Binary, compressed, and lock files are skipped.
    """
    binary_extensions = {
        ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff", ".woff2",
        ".ttf", ".eot", ".pdf", ".zip", ".tar", ".gz", ".bz2", ".7z",
        ".sqlite", ".db", ".bin", ".exe", ".dll", ".so", ".dylib",
        ".parquet", ".pkl", ".pickle", ".onnx", ".pt", ".safetensors",
    }
    skip_names = {
        ".gitignore", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
        "poetry.lock", "Pipfile.lock", "uv.lock",
    }
    lower = path.lower()
    if any(lower.endswith(ext) for ext in binary_extensions):
        return False
    if any(lower.endswith(name.lower()) for name in skip_names):
        return False
    return True
