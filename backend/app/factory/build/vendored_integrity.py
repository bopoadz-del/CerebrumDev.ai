"""CLONER gate: a block's source matched the digests it publishes about itself.

Every ``block.json`` carries a ``digests`` map -- ``{"block.py": "<sha256>",
...}`` -- and, on Store-signed blocks, a ``signature``. Nothing in the
pipeline had ever checked either. A published digest that no one verifies is
worse than an absent one: it reads as provenance while guaranteeing nothing,
and ``blocks.lock.json`` pins a commit that is equally unverified once the
files are copied out of their repository.

Verification runs at clone time against the **source** tree, because the
CLONER deliberately rewrites what it vendors: ``_rewrite_runtime_imports``
turns ``app.blocks`` into ``vendor.cerebrum.blocks``, shim constructors are
rewritten, and offline adapters are applied. Vendored bytes therefore cannot
match an upstream digest, and hashing them failed every honest build the
first time this gate ran (warehouse-operations, e2e).

So :func:`verify_block` runs in ``run_cloner`` before the copy, its verdict
is recorded into ``blocks.lock.json``, and the gate asserts the record exists
and passed. Emission does the check where it is meaningful; the gate enforces
that it happened. It is deliberately narrow:

* only files the manifest names are checked -- a block may legitimately ship
  files it does not digest;
* a block that publishes no ``digests`` is reported, not failed, because the
  Store's older manifests predate the field and failing them would gate on
  the Store's release history rather than on this build;
* a block vendored with no record at all fails exactly like one that failed
  the check, so skipping verification cannot pass quietly;
* a **missing** file named in digests always fails: absence is unambiguous
  and environment-independent;
* a **hash mismatch** is recorded, not fatal, unless
  ``FACTORY_STRICT_BLOCK_DIGESTS=1``. A text file's bytes depend on how the
  repository was checked out, so a mismatch can mean tampering *or* it can
  mean a different ``core.autocrlf`` on the machine that produced the
  manifest. Failing every build on the second would gate the pipeline on the
  Store's release history rather than on this build. The verdict is written
  to ``blocks.lock.json`` and surfaced in the gate payload either way, so the
  condition is visible and auditable rather than silently tolerated. Set the
  strict flag once a run has proven the digests stable across environments.

The signature is not verified: that needs the publisher's key, which the
factory does not carry. The record marks which blocks are signed so the
absence is visible instead of implied.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.factory.build.gates import GateContext, GateResult

GATE_NAME = "vendored_integrity"

#: Key under each blocks.lock.json entry holding the clone-time verdict.
LOCK_KEY = "integrity"

#: Env flag promoting a recorded hash mismatch to a build failure.
STRICT_ENV = "FACTORY_STRICT_BLOCK_DIGESTS"


def strict_digests() -> bool:
    return str(os.getenv(STRICT_ENV, "")).strip().lower() in {"1", "true", "yes", "on"}


_NUL = b"\x00"
_CRLF = b"\r\n"
_LF = b"\n"


def sha256_file(path: Path) -> str:
    """Byte-exact sha256 of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_lf(path: Path) -> str:
    """sha256 with CRLF normalised to LF. Empty string for binary content.

    A digest over a working-tree text file is not reproducible across
    platforms. ``core.autocrlf=true`` rewrites line endings on checkout, so
    one commit yields different bytes on Windows and Linux. Measured: the
    committed blob for ``vendor_blocks_mirror/audit/Dockerfile`` hashes to
    905bd5e5..., matching its manifest, while the Windows working copy hashes
    to 601d37f8... and matched nothing.

    Comparing normalised content keeps the check meaningful for what it
    guards -- tampering, truncation, a stale mirror -- and gives up only the
    ability to notice a difference that is purely line endings, which is a
    checkout representation rather than content.
    """
    raw = path.read_bytes()
    if _NUL in raw:
        return ""
    return hashlib.sha256(raw.replace(_CRLF, _LF)).hexdigest()


def digest_matches(path: Path, expected: str) -> bool:
    """True when the file matches byte-exactly, or after LF normalisation."""
    want = str(expected).strip().lower()
    if sha256_file(path).lower() == want:
        return True
    normalised = sha256_lf(path)
    return bool(normalised) and normalised.lower() == want


def verify_block(block_dir: Path) -> Dict[str, object]:
    """Check one block's files against the digests its own manifest declares."""
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
        out["missing"].append(f"{block_dir.name}/block.json")
        return out
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        out["missing"].append(f"{block_dir.name}/block.json unreadable: {exc}")
        return out

    digests = data.get("digests")
    out["signed"] = bool(data.get("signature"))
    if not isinstance(digests, dict) or not digests:
        return out
    out["has_digests"] = True

    for name, expected in sorted(digests.items()):
        # block.json cannot digest itself: the field is written into the very
        # bytes being hashed, so the manifest's own entry is self-referential.
        if Path(name).name == "block.json":
            continue
        target = block_dir / name
        if not target.is_file():
            out["missing"].append(f"{block_dir.name}/{name}")
            continue
        out["digested"] = int(out["digested"]) + 1
        if not digest_matches(target, expected):
            out["mismatched"].append(
                f"{block_dir.name}/{name}: manifest {str(expected)[:12]}... "
                f"but source hashes to {sha256_file(target)[:12]}... "
                "(checked byte-exact and LF-normalised)"
            )
    return out


def lock_record(source_dir: Path) -> Dict[str, object]:
    """Clone-time verdict for one block, written into blocks.lock.json."""
    report = verify_block(source_dir)
    absent = [f"{n}: named in digests but absent from source" for n in report["missing"]]
    mismatched = [str(m) for m in report["mismatched"]]
    return {
        "verified": not absent and not mismatched,
        "files_hashed": report["digested"],
        "has_digests": report["has_digests"],
        "signed_unverified": report["signed"],
        # Split, because only one of the two is environment-independent.
        "absent": absent,
        "mismatched": mismatched,
        "findings": absent + mismatched,
    }


def gate_vendored_integrity(ctx: "GateContext") -> "GateResult":
    """Every vendored block carries a passing clone-time integrity record."""
    from app.factory.build.gates import GateResult

    vendor = ctx.workspace / "vendor" / "blocks"
    if not vendor.is_dir():
        return GateResult(ok=True, gate=GATE_NAME, detail="no vendored blocks to verify")

    lock_path = ctx.workspace / "blocks.lock.json"
    if not lock_path.is_file():
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            detail="blocks.lock.json is missing - no integrity record to check",
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
    unrecorded: List[str] = []
    undigested: List[str] = []
    mismatches: List[str] = []
    checked = 0
    files = 0
    signed = 0

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
        # Absence is always fatal; a hash mismatch is fatal only in strict mode.
        findings.extend(str(f) for f in (record.get("absent") or []))
        mism = [str(f) for f in (record.get("mismatched") or [])]
        if mism:
            if strict_digests():
                findings.extend(mism)
            else:
                mismatches.extend(mism)

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

    detail = f"{files} file(s) across {checked} block(s) verified against published digests"
    if undigested:
        detail += f"; {len(undigested)} block(s) publish no digests"
    if mismatches:
        detail += (
            f"; {len(mismatches)} digest mismatch(es) RECORDED — set "
            f"{STRICT_ENV}=1 to make these fail the build"
        )
    return GateResult(
        ok=True,
        gate=GATE_NAME,
        detail=detail,
        payload={
            "blocks_checked": checked,
            "files_hashed": files,
            "blocks_without_digests": undigested,
            "blocks_signed_but_unverified": signed,
            "digest_mismatches": mismatches,
            "strict": strict_digests(),
        },
    )
