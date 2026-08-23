"""CLONER gate: vendored bytes match the digests the block itself publishes.

Every ``block.json`` carries a ``digests`` map -- ``{"block.py": "<sha256>",
...}`` -- and, on Store-signed blocks, a ``signature``. Nothing in the
pipeline has ever checked either. A published digest that no one verifies is
worse than an absent one: it reads as provenance while guaranteeing nothing,
and ``blocks.lock.json`` pins a commit that is equally unverified once the
files are copied out of their repository.

This gate re-hashes what was actually vendored and compares it with what the
block claims about itself. It is deliberately narrow:

* only files the manifest names are checked -- a block may legitimately ship
  files it does not digest;
* a block that publishes no ``digests`` is reported, not failed, because the
  Store's older manifests predate the field and failing them would gate on
  the Store's release history rather than on this build;
* a mismatch is always a failure. That is tampering, truncation, or a stale
  vendor mirror, and none of the three should reach a customer.

The signature is not verified here: that needs the publisher's key, which
the factory does not carry. The gate records which blocks are signed so the
absence is visible instead of implied.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.factory.build.gates import GateContext, GateResult

GATE_NAME = "vendored_integrity"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_block(block_dir: Path) -> Dict[str, object]:
    """Re-hash one vendored block against its own manifest."""
    out: Dict[str, object] = {
        "block_id": block_dir.name,
        "digested": 0,
        "mismatched": [],
        "missing": [],
        "has_digests": False,
        "signed": False,
    }
    manifest = block_dir / "block.json"
    if not manifest.is_file():
        out["missing"].append("block.json")
        return out
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        out["missing"].append(f"block.json unreadable: {exc}")
        return out

    digests = data.get("digests")
    out["signed"] = bool(data.get("signature"))
    if not isinstance(digests, dict) or not digests:
        return out
    out["has_digests"] = True

    for name, expected in sorted(digests.items()):
        target = block_dir / name
        if not target.is_file():
            out["missing"].append(f"{block_dir.name}/{name}")
            continue
        # block.json cannot digest itself: the field is written into the very
        # bytes being hashed, so the manifest's own entry is self-referential.
        if Path(name).name == "block.json":
            continue
        actual = sha256_file(target)
        out["digested"] = int(out["digested"]) + 1
        if actual.lower() != str(expected).lower():
            out["mismatched"].append(
                f"{block_dir.name}/{name}: manifest {str(expected)[:12]}… "
                f"but vendored bytes hash to {actual[:12]}…"
            )
    return out


def gate_vendored_integrity(ctx: "GateContext") -> "GateResult":
    """Vendored block bytes match the digests those blocks publish."""
    from app.factory.build.gates import GateResult

    vendor = ctx.workspace / "vendor" / "blocks"
    if not vendor.is_dir():
        return GateResult(
            ok=True,
            gate=GATE_NAME,
            detail="no vendored blocks to verify",
        )

    findings: List[str] = []
    checked = 0
    files = 0
    undigested: List[str] = []
    signed = 0

    for block_dir in sorted(p for p in vendor.iterdir() if p.is_dir()):
        report = verify_block(block_dir)
        checked += 1
        files += int(report["digested"])
        if report["signed"]:
            signed += 1
        if not report["has_digests"]:
            undigested.append(block_dir.name)
        findings.extend(str(m) for m in report["mismatched"])
        findings.extend(
            f"{name}: named in digests but not vendored" for name in report["missing"]
        )

    if findings:
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            detail="vendored bytes do not match the digests the block publishes",
            findings=findings[:20],
            payload={"blocks_checked": checked, "files_hashed": files},
        )

    detail = f"{files} file(s) across {checked} block(s) match their published digests"
    if undigested:
        detail += f"; {len(undigested)} block(s) publish no digests"
    return GateResult(
        ok=True,
        gate=GATE_NAME,
        detail=detail,
        payload={
            "blocks_checked": checked,
            "files_hashed": files,
            "blocks_without_digests": undigested,
            "blocks_signed_but_unverified": signed,
        },
    )
