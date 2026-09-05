"""PRODUCT one-record persist / re-read contract for C-BRIEF.

Photographed Floor (2026-09-05, platforms-poll5 / sess_108e101): after a
successful Kimi FACTORY_CODE_CLI WRITER, TESTER stopped:

    PRODUCT (one-record round-trip): 3 capability(ies) did not remember
    a record they were given.

Verified findings (not assumed):

    audit: POST raised OperationalError: no such table: audit
    dashboard: POST raised OperationalError: no such table: dashboard
    veterinary_care_core: POST raised OperationalError: no such table:
        veterinary_care_core

Those ids are the keyword-fallback architect roster (``{vertical}_core``
GENERATE + mentioned Store blocks as REUSE; ``audit`` is always added).
WRITER ``writer_behaviour`` isolates ``STORAGE_PATH`` to a tempfile, so it
migrates the *current* alembic ``0001`` and can go green. PRODUCT's
round-trip probe used the workspace ``./data`` file. A leftover
``platform.db`` already stamped at ``0001_baseline`` (CLI ran alembic, or
a prior TestClient) makes ``upgrade_head()`` a no-op after the factory
overwrites ``0001`` with the persist entities — ``store.save(entity)``
then raises ``OperationalError``.

This module is the one brief + emit + harness contract. An LLM never
writes these rules. Isolated schema POST-raises must not let WRITER claim
done while PRODUCT will refuse the same persist path.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from app.factory.build.data_lifecycle import (
    IDEMPOTENCY_TABLE,
    WORK_QUEUE_TABLE,
    migration_table_names,
)
from app.factory.build.product_gate import GATE_SCOPES

PRODUCT_ROUND_TRIP_CHECK = "round_trip"
PRODUCT_ROUND_TRIP_HALT = (
    "did not remember a record they were given"
)
PRODUCT_NO_SUCH_TABLE_HALT = "no such table"
PRODUCT_POST_RAISED_HALT = "POST raised"
WRITER_PERSIST_HALT = (
    "WRITER [check:round_trip] failed — persist entity missing from "
    "alembic 0001 or store.COLUMNS"
)
PERSIST_ISOLATE_NEEDLE = "tempfile.mkdtemp(prefix=\"product-gate-\")"
FACTORY_GROUNDED_PERSIST_SOURCE = "factory-grounded persist"

#: Photographed keyword-fallback Veterinary Care Platform roster.
KEYWORD_FALLBACK_VETCARE_CAPS = (
    "veterinary_care_core",
    "audit",
    "dashboard",
)

_SKIP_ALEMBIC_TABLES = {WORK_QUEUE_TABLE, IDEMPOTENCY_TABLE}


class PersistRoundTripHalt(ValueError):
    """WRITER must not claim done: persist entities are not durable."""


def persist_entity_of(
    spec: Optional[Mapping[str, Any]],
    capability_id: str,
) -> str:
    """Store / alembic table the PRODUCT probe will re-read."""
    raw = ""
    if isinstance(spec, Mapping):
        raw = str(spec.get("entity") or "").strip()
    if not raw:
        raw = str(capability_id or "record")
    return raw.replace("-", "_")


def persist_accept_rules_text() -> str:
    """BUILD cut: what PRODUCT one-record will POST and what persist means."""
    caps = ", ".join(KEYWORD_FALLBACK_VETCARE_CAPS)
    return "\n".join(
        [
            "PRODUCT gate one-record round-trip (after WRITER, before STORE):",
            "The harness boots the product (TestClient lifespan runs alembic)",
            "on an isolated STORAGE_PATH, then POSTs /v1/{capability_id} with a",
            "payload built from that capability's own FIELDS + CONSTRAINTS.",
            "Accept means HTTP 200, not ok:false, store.list_all(entity) holds",
            "the record, and GET /v1/{capability_id} returns it.",
            f"A miss is reported as: {PRODUCT_ROUND_TRIP_HALT}",
            f"POST raising OperationalError: {PRODUCT_NO_SUCH_TABLE_HALT}: "
            "<entity> is that miss — the persist table was not migrated.",
            "",
            "factory-grounded persist (not an LLM stub):",
            "- every capability has an alembic 0001 table named spec.entity",
            "  (capability_id with '-' → '_' when the spec omits entity)",
            "- store.COLUMNS and store.save use that same entity",
            "- handle() persists via store.save(ENTITY, payload) after blocks",
            "  succeed (GENERATE with no blocks still persists)",
            "- the route save(payload) writes the request, not handle()'s envelope",
            "- do not persist to 'records' or a capability id that is not the entity",
            "- do not rely on a leftover ./data/platform.db; PRODUCT isolates",
            "  STORAGE_PATH the same way writer_behaviour does",
            "",
            "Keyword-fallback architect rosters (live Veterinary Care Platform):",
            f"  {caps}",
            "Those ids are persistable capabilities, not 'just blocks'. Each",
            "must remember one record. Templated execute(block_id, payload)",
            "or no_block_bound without store.save is not done.",
        ]
    )


def persist_accept_acceptance_line() -> str:
    """ACCEPTANCE cut: harness check, not a coder decorative test."""
    return (
        "- every emitted capability persists one record to its alembic "
        "entity and GET returns it "
        f"({GATE_SCOPES['PRODUCT']})  "
        f"[check:{PRODUCT_ROUND_TRIP_CHECK}]"
    )


def persist_accept_forbidden_lines() -> str:
    """FORBIDDEN cut: the live CLI inventions that PRODUCT then refuses."""
    return "\n".join(
        [
            "- persist to a table alembic 0001 did not create "
            f"({PRODUCT_NO_SUCH_TABLE_HALT}: <entity>)",
            "- leaving PRODUCT on a leftover ./data/platform.db stamped at "
            "0001_baseline after the factory rewrote 0001 (upgrade_head no-op)",
            "- execute(block_id, payload) or no_block_bound stubs that never "
            "store.save(ENTITY, payload) (keyword-fallback audit / dashboard / "
            "{vertical}_core class)",
            "- persist to table=records or to the capability id when spec.entity "
            "is a different name",
            "- treating WRITER writer_behaviour green on a tempfile DB as "
            f"done while PRODUCT still {PRODUCT_ROUND_TRIP_HALT}",
        ]
    )


def persist_accept_brief_contract() -> str:
    """System-brief paragraph shared by WRITER seat + HTTP oneshot."""
    return (
        "PRODUCT one-record round-trip POSTs a schema-sample payload then "
        "re-reads store.list_all(entity) and GET /v1/{capability_id}. A miss "
        f"is {PRODUCT_ROUND_TRIP_HALT!r}. Every capability must persist that "
        "record to its alembic entity via store.save(ENTITY, payload) "
        "(factory-grounded persist). "
        f"POST {PRODUCT_POST_RAISED_HALT} "
        f"OperationalError: {PRODUCT_NO_SUCH_TABLE_HALT}: <entity> is a miss. "
        "WRITER isolates STORAGE_PATH; PRODUCT must too — a leftover "
        "./data/platform.db already at 0001_baseline is not a pass. "
        f"Keyword-fallback {', '.join(KEYWORD_FALLBACK_VETCARE_CAPS)} "
        "are persistable capabilities."
    )


def persist_accept_needles() -> Sequence[str]:
    """Needles lint requires on every compiled brief."""
    return (
        "one-record round-trip",
        PRODUCT_ROUND_TRIP_HALT,
        PRODUCT_NO_SUCH_TABLE_HALT,
        "store.save(ENTITY, payload)",
        "factory-grounded persist",
        "STORAGE_PATH",
        "0001_baseline",
        f"[check:{PRODUCT_ROUND_TRIP_CHECK}]",
        "alembic entity",
    )


def persist_entities_from_specs(
    specs: Mapping[str, Mapping[str, Any]],
) -> Dict[str, str]:
    """capability_id → persist entity."""
    return {
        str(cid): persist_entity_of(spec, str(cid))
        for cid, spec in (specs or {}).items()
        if str(cid).strip()
    }


def missing_alembic_persist_entities(
    revision_0001_source: str,
    entities: Iterable[str],
) -> List[str]:
    created = migration_table_names(revision_0001_source or "")
    want = {str(e).strip() for e in entities if str(e).strip()}
    return sorted(want - created)


def wipe_workspace_runtime_db(workspace: Any) -> None:
    """Drop leftover ./data so the next boot migrates current 0001."""
    root = Path(getattr(workspace, "workspace", workspace))
    data = root / "data"
    if data.is_dir():
        shutil.rmtree(data)


def persist_round_trip_errors(
    root: Path,
    specs: Mapping[str, Mapping[str, Any]],
) -> List[str]:
    """Scan emitted alembic + store + handlers/routes. Empty = green."""
    base = Path(root)
    errors: List[str] = []
    entities = persist_entities_from_specs(specs)
    revision = base / "alembic" / "versions" / "0001_baseline.py"
    if not revision.is_file():
        return ["alembic/versions/0001_baseline.py is missing"]
    try:
        src = revision.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"alembic 0001 unreadable: {exc}"]
    missing = missing_alembic_persist_entities(src, entities.values())
    if missing:
        errors.append(
            "alembic 0001 missing persist table(s): " + ", ".join(missing)
        )

    store_py = base / "app" / "store.py"
    if store_py.is_file():
        try:
            store_src = store_py.read_text(encoding="utf-8")
        except OSError:
            store_src = ""
        for entity in sorted(set(entities.values())):
            if f'"{entity}"' not in store_src and f"'{entity}'" not in store_src:
                errors.append(f"store.COLUMNS missing persist entity {entity}")
    else:
        errors.append("app/store.py is missing")

    routes = base / "app" / "routes.py"
    route_src = ""
    if routes.is_file():
        try:
            route_src = routes.read_text(encoding="utf-8")
        except OSError:
            route_src = ""
    if route_src and "save(payload)" not in route_src:
        errors.append("app/routes.py does not persist via save(payload)")

    for cid, entity in entities.items():
        name = cid.replace("-", "_")
        handler = base / "app" / "actions" / f"{name}.py"
        if not handler.is_file():
            errors.append(f"handler missing for persist capability {cid}")
            continue
        try:
            text = handler.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"handler {cid} unreadable: {exc}")
            continue
        persists = (
            "_persist_record(" in text
            or "store.save(ENTITY" in text
            or f'store.save("{entity}"' in text
            or f"store.save('{entity}'" in text
        )
        if not persists:
            errors.append(
                f"{cid}: handler does not store.save persist entity {entity}"
            )
        wrong = re.findall(r"store\.save\(\s*['\"]([^'\"]+)['\"]", text)
        for table in wrong:
            if table not in {entity, "ENTITY"} and table not in _SKIP_ALEMBIC_TABLES:
                errors.append(
                    f"{cid}: persists to {table!r}, alembic entity is {entity!r}"
                )
    return errors


def assert_persist_round_trip_ready(
    root: Path,
    specs: Mapping[str, Mapping[str, Any]],
) -> None:
    errors = persist_round_trip_errors(root, specs)
    if errors:
        raise PersistRoundTripHalt(
            WRITER_PERSIST_HALT + ": " + "; ".join(errors[:8])
        )


def grounded_persist_assign() -> str:
    """One line: persist the request to the capability entity."""
    return "    stored = _persist_record(payload)"
