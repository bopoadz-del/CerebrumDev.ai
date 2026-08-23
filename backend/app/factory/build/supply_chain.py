"""Fail-closed supply-chain checks for Factory emitters.

:latest is never a pin. An image reference without ``@sha256:<64 hex>`` is
unverifiable. Block ids must already exist in the factory vendor mirror or a
provided Blocks checkout — this module does not invent ids or digests.

The Python base-image digest was fetched from Docker Hub
``GET /v2/repositories/library/python/tags/3.12-slim`` on 2026-08-23
(Hub ``last_updated`` 2026-08-16T20:07:22Z). It is the tag's manifest-list
digest, not an invented hash.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

# Manifest-list digest for library/python:3.12-slim (Docker Hub, 2026-08-23).
PYTHON_312_SLIM_DIGEST = (
    "sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a"
)
PYTHON_312_SLIM_FROM = f"python:3.12-slim@{PYTHON_312_SLIM_DIGEST}"
PYTHON_312_SLIM_PIN_SOURCE = (
    "https://hub.docker.com/v2/repositories/library/python/tags/3.12-slim"
)

_LATEST_RE = re.compile(r":latest(?:[^\w.-]|$)")
_DIGEST_RE = re.compile(r"@sha256:[0-9a-f]{64}\b")
_FROM_RE = re.compile(r"^\s*FROM\s+(\S+)", re.MULTILINE | re.IGNORECASE)

_VENDOR_MIRROR = Path(__file__).resolve().parents[1] / "vendor_blocks_mirror"


class SupplyChainError(ValueError):
    """Unverifiable pin, :latest, or invented block id."""


def findings_for_image_ref(ref: str, *, loc: str) -> List[str]:
    ref = (ref or "").strip()
    if not ref:
        return [f"{loc}: empty image reference"]
    if ref.endswith(":latest") or _LATEST_RE.search(ref):
        return [f"{loc}: :latest is not a pin ({ref})"]
    if not _DIGEST_RE.search(ref):
        return [f"{loc}: image is not digest-pinned ({ref})"]
    return []


def scan_dockerfile(text: str, *, loc: str = "Dockerfile") -> List[str]:
    findings: List[str] = []
    for match in _FROM_RE.finditer(text or ""):
        findings.extend(findings_for_image_ref(match.group(1), loc=f"{loc} FROM"))
    if not findings and not _FROM_RE.search(text or ""):
        findings.append(f"{loc}: no FROM line")
    return findings


def assert_generated_dockerfile(text: str, *, loc: str = "Dockerfile") -> None:
    findings = scan_dockerfile(text, loc=loc)
    if findings:
        raise SupplyChainError("; ".join(findings))


def scan_block_manifest(data: Dict[str, Any], *, loc: str) -> List[str]:
    findings: List[str] = []
    execution = data.get("execution") if isinstance(data.get("execution"), dict) else {}
    image = None
    if isinstance(execution, dict):
        image = execution.get("image")
    if not image:
        image = data.get("image")
    if image:
        findings.extend(findings_for_image_ref(str(image), loc=f"{loc} image"))
    return findings


def known_factory_block_ids(*, extra_roots: Sequence[Optional[Path]] = ()) -> frozenset:
    ids: set[str] = set()
    if _VENDOR_MIRROR.is_dir():
        for path in _VENDOR_MIRROR.iterdir():
            if path.is_dir() and (path / "block.py").is_file():
                ids.add(path.name)
    for root in extra_roots:
        if not root:
            continue
        base = Path(root)
        for candidate in (base / "block_registry", base / "block_store", base):
            if not candidate.is_dir():
                continue
            for path in candidate.iterdir():
                if path.is_dir() and (path / "block.py").is_file():
                    ids.add(path.name)
    return frozenset(ids)


def assert_known_block_ids(
    block_ids: Iterable[str],
    *,
    extra_roots: Sequence[Optional[Path]] = (),
) -> None:
    known = known_factory_block_ids(extra_roots=extra_roots)
    unknown = sorted({bid for bid in block_ids if bid and bid not in known})
    if unknown:
        raise SupplyChainError(
            "unknown block id(s) — do not invent: " + ", ".join(unknown)
        )


def redact_unpinned_images(block_json_path: Path) -> bool:
    """Refuse unverifiable image pins in a cloned block.json.

    Does not invent a replacement digest. Returns True when the file changed.
    """
    try:
        data = json.loads(block_json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    findings = scan_block_manifest(data, loc=block_json_path.name)
    if not findings:
        return False
    execution = data.get("execution")
    if isinstance(execution, dict) and execution.get("image"):
        execution = dict(execution)
        execution.pop("image", None)
        execution["image_pin"] = "refused_unverified"
        data["execution"] = execution
    if data.get("image"):
        data.pop("image", None)
        data["image_pin"] = "refused_unverified"
    block_json_path.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return True

