"""N1b — deterministic receipt-vs-diff + path-jail (not an LLM).

A clean receipt+diff is **not** green. It only authorizes a handoff to the
N3 store gate (``scripts/acceptance.py`` in Docker). Two failure classes
must never be conflated (run9 lesson):

* ``EXECUTOR_UNAVAILABLE`` — infra (agent never started / hung). Lived in
  :mod:`app.factory.build.cli_pivot`.
* ``RECEIPT_INVALID`` / ``PATHS_VIOLATED`` — the agent ran; output is bad.

``cli_authored_ids`` must equal the blueprint capability set. Missing or
extra ids are ``RECEIPT_INVALID`` with no partial credit and no template
fallback.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

from app.factory.build.authority import (
    ROLE_CONTRACTS,
    SEALED_AFTER_CLONER,
    BuildRole,
    _matches_lane,
)

RECEIPT_INVALID = "RECEIPT_INVALID"
PATHS_VIOLATED = "PATHS_VIOLATED"
HANDOFF_TO_N3 = "HANDOFF_TO_N3"

RECEIPT_SCHEMA = "cli_receipt.v1"
RECEIPT_NAMES = ("receipt.json", "docs/receipt.json", "docs/coder_receipt.json")

#: Unified-diff path lines produced by ``git diff`` / ``git show``.
_DIFF_GIT_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")
_DIFF_PLUS_RE = re.compile(r"^\+\+\+ b/(.+)$")
_DIFF_MINUS_RE = re.compile(r"^--- a/(.+)$")


class ReceiptInvalid(ValueError):
    """Agent ran; receipt missing, malformed, or ids do not match the set."""

    honesty = RECEIPT_INVALID


class PathsViolated(ValueError):
    """Agent ran; diff edits a sealed or out-of-lane path."""

    honesty = PATHS_VIOLATED


def blueprint_capability_set(blueprint: Any) -> Set[str]:
    """Capability ids the receipt must match exactly (no partial credit)."""
    ids: List[str] = []
    for cap in getattr(blueprint, "capabilities", ()) or ():
        cid = str(
            getattr(cap, "id", "")
            or getattr(cap, "capability_id", "")
            or ""
        ).strip()
        if cid and cid not in ids:
            ids.append(cid)
    return set(ids)


def handler_relpath(capability_id: str) -> str:
    """Default file a capability id must appear as in the branch diff."""
    return f"app/actions/{str(capability_id).replace('-', '_')}.py"


def writer_allowed_globs() -> tuple[str, ...]:
    """WRITER lanes plus BA ``tests/**`` and the N1 receipt files.

    CHADi 2026-09-12 Option A: the cerebrum-builds / cli-pivot BA jail
    allows ``tests/**``. Vendor trees, ``blocks.lock.json``, the ledger,
    and ``.git`` stay sealed. Factory WRITER role lanes in
    :mod:`authority` are unchanged (TESTER still owns tests there).
    """
    lanes = [glob for _root, glob in ROLE_CONTRACTS[BuildRole.WRITER].write_lanes]
    extra = ["tests/**", "receipt.json", "docs/receipt.json"]
    seen: List[str] = []
    for glob in lanes + extra:
        if glob not in seen:
            seen.append(glob)
    return tuple(seen)


def sealed_globs() -> tuple[str, ...]:
    """Vendored blocks are read-only; diff-verified."""
    return (
        *SEALED_AFTER_CLONER,
        "vendor_blocks/**",
        "vendor_blocks_mirror/**",
        "blocks.lock.json",
        "build_ledger.jsonl",
        ".git/**",
    )


def _posix(path: str | Path) -> str:
    """Normalize slashes and a ``./`` prefix. Do not strip ``.git`` / ``.env``."""
    text = str(path).replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def path_matches_any(rel: str, globs: Sequence[str]) -> bool:
    posix = Path(_posix(rel))
    return any(_matches_lane(posix, glob) for glob in globs)


def parse_changed_paths(
    changed_paths: Optional[Sequence[str]] = None,
    unified_diff: Optional[str] = None,
) -> List[str]:
    """Branch-diff paths. Receipt is the claim; this list is the check."""
    found: List[str] = []

    def _add(raw: str) -> None:
        path = _posix(raw)
        if not path or path == "/dev/null":
            return
        if path not in found:
            found.append(path)

    for item in changed_paths or ():
        _add(str(item))
    for line in (unified_diff or "").splitlines():
        git = _DIFF_GIT_RE.match(line)
        if git:
            _add(git.group(2))
            continue
        plus = _DIFF_PLUS_RE.match(line)
        if plus:
            _add(plus.group(1))
            continue
        minus = _DIFF_MINUS_RE.match(line)
        if minus:
            _add(minus.group(1))
    return found


def load_receipt(raw: Any) -> Dict[str, Any]:
    """Require a well-formed receipt object. Missing/garbage → RECEIPT_INVALID."""
    if raw is None:
        raise ReceiptInvalid(f"{RECEIPT_INVALID}: receipt.json missing")
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise ReceiptInvalid(f"{RECEIPT_INVALID}: receipt.json empty")
        try:
            raw = json.loads(text)
        except ValueError as exc:
            raise ReceiptInvalid(
                f"{RECEIPT_INVALID}: receipt.json is not well-formed JSON: {exc}"
            ) from exc
    if not isinstance(raw, Mapping):
        raise ReceiptInvalid(f"{RECEIPT_INVALID}: receipt.json is not an object")
    if "cli_authored_ids" not in raw:
        raise ReceiptInvalid(
            f"{RECEIPT_INVALID}: receipt.json missing cli_authored_ids"
        )
    ids_raw = raw.get("cli_authored_ids")
    if not isinstance(ids_raw, Sequence) or isinstance(ids_raw, (str, bytes)):
        raise ReceiptInvalid(
            f"{RECEIPT_INVALID}: cli_authored_ids must be a list"
        )
    ids: List[str] = []
    for item in ids_raw:
        cid = str(item or "").strip()
        if cid and cid not in ids:
            ids.append(cid)
    path_by_id: Dict[str, str] = {}
    extra_map = raw.get("path_by_id") or raw.get("files") or {}
    if extra_map and not isinstance(extra_map, Mapping):
        raise ReceiptInvalid(f"{RECEIPT_INVALID}: path_by_id must be an object")
    if isinstance(extra_map, Mapping):
        for key, value in extra_map.items():
            cid = str(key or "").strip()
            rel = _posix(value)
            if cid and rel:
                path_by_id[cid] = rel
    return {
        "schema": str(raw.get("schema") or RECEIPT_SCHEMA),
        "cli_authored_ids": ids,
        "path_by_id": path_by_id,
    }


def read_receipt_file(root: Path | str) -> Dict[str, Any]:
    base = Path(root)
    for name in RECEIPT_NAMES:
        path = base / name
        if path.is_file():
            return load_receipt(path.read_text(encoding="utf-8"))
    raise ReceiptInvalid(f"{RECEIPT_INVALID}: receipt.json missing")


def assert_ids_equal_blueprint(
    authored: Iterable[str],
    blueprint_ids: Iterable[str],
) -> None:
    """Set equality. Missing or extra = FAILED. No partial credit."""
    claimed = {str(x).strip() for x in authored if str(x).strip()}
    required = {str(x).strip() for x in blueprint_ids if str(x).strip()}
    missing = sorted(required - claimed)
    extra = sorted(claimed - required)
    if missing or extra:
        parts = []
        if missing:
            parts.append("missing=" + ",".join(missing))
        if extra:
            parts.append("extra=" + ",".join(extra))
        raise ReceiptInvalid(
            f"{RECEIPT_INVALID}: cli_authored_ids must equal the blueprint "
            f"capability set ({'; '.join(parts)})"
        )


def assert_ids_in_diff(
    authored: Sequence[str],
    changed_paths: Sequence[str],
    path_by_id: Optional[Mapping[str, str]] = None,
) -> None:
    """Each claimed id must map to a real file in the branch diff."""
    changed = {_posix(p) for p in changed_paths}
    mapping = dict(path_by_id or {})
    missing_files: List[str] = []
    for cid in authored:
        rel = mapping.get(cid) or handler_relpath(cid)
        if rel not in changed:
            missing_files.append(f"{cid}->{rel}")
    if missing_files:
        raise ReceiptInvalid(
            f"{RECEIPT_INVALID}: cli_authored_ids claim files not in the "
            f"branch diff: {', '.join(missing_files)}"
        )


def assert_path_jail(changed_paths: Sequence[str]) -> None:
    """No edits outside allowed WRITER lanes; vendored trees stay read-only."""
    allowed = writer_allowed_globs()
    sealed = sealed_globs()
    sealed_hits: List[str] = []
    outside: List[str] = []
    for raw in changed_paths:
        rel = _posix(raw)
        if path_matches_any(rel, sealed):
            sealed_hits.append(rel)
            continue
        if not path_matches_any(rel, allowed):
            outside.append(rel)
    if sealed_hits:
        raise PathsViolated(
            f"{PATHS_VIOLATED}: vendored/sealed paths are read-only: "
            + ", ".join(sealed_hits)
        )
    if outside:
        raise PathsViolated(
            f"{PATHS_VIOLATED}: edits outside allowed paths: "
            + ", ".join(outside)
        )


@dataclass(frozen=True)
class ReceiptVerdict:
    """Enforcement result. ``green`` is always False — N3 grades the artifact."""

    honesty: str
    green: bool = False
    next: Optional[str] = None
    cli_authored_ids: List[str] = field(default_factory=list)
    changed_paths: List[str] = field(default_factory=list)
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.honesty == HANDOFF_TO_N3

    def to_dict(self) -> Dict[str, Any]:
        return {
            "honesty": self.honesty,
            "green": False,
            "next": self.next,
            "cli_authored_ids": list(self.cli_authored_ids),
            "changed_paths": list(self.changed_paths),
            "detail": self.detail,
            "ok": self.ok,
        }


def enforce_receipt(
    *,
    blueprint: Any = None,
    blueprint_ids: Optional[Iterable[str]] = None,
    receipt: Any = None,
    workspace: Optional[Path | str] = None,
    changed_paths: Optional[Sequence[str]] = None,
    unified_diff: Optional[str] = None,
) -> ReceiptVerdict:
    """Receipt is the claim; the branch diff is the check. Never marks green."""
    required = (
        set(blueprint_ids)
        if blueprint_ids is not None
        else blueprint_capability_set(blueprint)
    )
    if receipt is None and workspace is not None:
        loaded = read_receipt_file(workspace)
    else:
        loaded = load_receipt(receipt)
    authored = list(loaded["cli_authored_ids"])
    assert_ids_equal_blueprint(authored, required)
    paths = parse_changed_paths(changed_paths, unified_diff)
    assert_ids_in_diff(authored, paths, loaded.get("path_by_id"))
    assert_path_jail(paths)
    return ReceiptVerdict(
        honesty=HANDOFF_TO_N3,
        green=False,
        next="n3_gate",
        cli_authored_ids=authored,
        changed_paths=list(paths),
        detail="receipt+diff clean — hand to N3 gate (not green)",
    )
