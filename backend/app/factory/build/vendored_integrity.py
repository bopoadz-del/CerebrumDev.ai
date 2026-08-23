"""CLONER gate: vendored bytes match the digests the block itself publishes.

Every ``block.json`` carries a ``digests`` map -- ``{"block.py": "<sha256>",
...}`` -- and, on Store-signed blocks, a ``signature``. Nothing in the
pipeline has ever checked either. A published digest that no one verifies is
worse than an absent one: it reads as provenance while guaranteeing nothing,
and ``blocks.lock.json`` pins a commit that is equally unverified once the
files are copied out of their repository.

Verification happens at clone time, against the **source** tree, because the
CLONER deliberately rewrites what it vendors: ``_rewrite_runtime_imports``
turns ``app.blocks`` into ``vendor.cerebrum.blocks``, shim constructors are
rewritten, and offline adapters are applied. Vendored bytes therefore cannot
match an upstream digest, and hashing them would fail every honest build --
as it did, on the warehouse-operations e2e, the first time this gate ran.

So :func:`verify_block` runs in ``run_cloner`` before the copy, its verdict
is recorded into ``blocks.lock.json``, and the gate asserts that the record
exists and passed. Emission does the check where it is meaningful; the gate
enforces that it happened. It is deliberately narrow:

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


LOCK_KEY = "integrity"


def lock_record(source_dir: Path) -> Dict[str, object]:
    """Clone-time verdict for one block, for blocks.lock.json."""
    report = verify_block(source_dir)
    return {
        "verified": not report["mismatched"] and not report["missing"],
        "files_hashed": report["digested"],
        "has_digests": report["has_digests"],
        "signed_unverified": report["signed"],
        "findings": [str(m) for m in report["mismatched"]]
        + [f"{n}: named in digests but absent from source" for n in report["missing"]],
    }


def gate_vendored_integrity(ctx: "GateContext") -> "GateResult":
    """Every vendored block carries a passing clone-time integrity record."""
    from app.factory.build.gates import GateResult

    vendor = ctx.workspace / "vendor" / "blocks"
    if not vendor.is_dir():
        return GateResult(
            ok=True,
            gate=GATE_NAME,
            detail="no vendored blocks to verify",
        )

    lock_path = ctx.workspace / "blocks.lock.json"
    if not lock_path.is_file():
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            detail="blocks.lock.json is missing — no integrity record to check",
            findings=["cloner wrote no lockfile"],
        )
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            detail=f"blocks.lock.json is unreadable: {exc}",
            findings=[str(exc)],
        )

    entries = lock.get("blocks") or {}
    findings: List[str] = []
    checked = 0
    files = 0
    undigested: List[str] = []
    signed = 0
    unrecorded: List[str] = []

    for block_dir in sorted(p for p in vendor.iterdir() if p.is_dir()):
        checked += 1
        record = (entries.get(block_dir.name) or {}).get(LOCK_KEY)
        if not isinstance(record, dict):
            unrecorded.append(block_dir.name)
            continue
        files += int(record.get("files_hashed") or 0)
        if record.get("signed_unverified"):
            signed += 1
        if not record.get("has_digests"):
            undigested.append(block_dir.name)
        if not record.get("verified"):
            findings.extend(str(f) for f in (record.get("findings") or []))

    if unrecorded:
        findings.extend(
            f"{name}: vendored with no clone-time integrity record" for name in unrecorded
        )

    if findings:
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            detail="a vendored block failed or skipped clone-time integrity verification",
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
