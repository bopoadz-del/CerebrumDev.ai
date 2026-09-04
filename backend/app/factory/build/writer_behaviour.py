"""WRITER behaviour gate: a route may not report success over a failed block.

``gate_workspace_compiles`` is the WRITER's only acceptance check, so the
role that authors every route, model and persistence path is accepted if
``app/`` parses. That is the hole LotDesk shipped through: three routes that
discarded ``handle()``'s result and persisted the request regardless, all of
them syntactically perfect.

The deterministic emitter is not the risk. ``_templated_route_body`` routes
through ``run_capability`` and persists only on ``ActionStatus.SUCCESS``.
The coder path writes its own body, and ``coder._validate_body`` asserts
syntax, sandbox-escape and a non-nested ``return`` -- nothing about the
contract. Whichever path produced the route, this gate judges the artifact.

Two phases, in order, because a one-phase probe passes for the wrong reason:

* **baseline** -- with the blocks working, a payload built from the entity's
  own declared constraints must be accepted. A route that rejects its own
  schema is a defect in itself, and without this phase an invalid payload
  would make phase two "pass" while never reaching a block at all.
* **forced failure** -- with every block call returning an error envelope,
  a route that answers ``ok: True`` or persists is a *per-capability miss*.
  One dishonest capability must not halt the whole WRITER phase (live
  invoice-management, 2026-08-30: four handlers written, then
  ``writer_behaviour`` stopped the run before TESTER/STORE_MANAGER, no zip).
  Isolated schema refusals and isolated F11 unused-block declarations
  are the same class of miss: they must not halt a mixed workspace.
  The gate fails only when *every* capability is dishonest (all refuse
  their own schema, every capability declares blocks it never invokes,
  or every block-reaching capability lies), or when the workspace
  cannot be probed. The Floor banner must name that reason (schema vs
  F11 vs F1) — never map a schema or F11 halt onto
  ``success over a failed block``. ``(F1)`` is a substring of
  ``(F11)``; the host must not treat an F11 finding as F1.

The seam is ``app.dispatch.execute``. Both the kernel path
(``run_capability`` -> ``execute_action`` -> handler) and a coder-written
handler bottom out there, so stubbing it covers both. Handlers bind the name
at import (``from app.dispatch import execute``), so the already-imported
module attributes are patched too.
"""

from __future__ import annotations

import re
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.factory.build.gates import GateContext, GateResult

GATE_NAME = "writer_behaviour"

#: Probe / Floor text when no capability accepts a payload from its own model.
SCHEMA_HALT = "no capability accepted its own schema"

#: Probe / Floor text when every block-reaching capability lies (LotDesk).
F1_HALT = "a capability route reported success over a failed block"

#: Probe / Floor text when every capability declares unused BLOCK_IDS (F11).
F11_HALT = "a capability declares block(s) it never invokes"

#: Probe / Floor text when every capability's blocks refuse the coder payload.
CONTRACT_HALT = "every capability wrote a payload its blocks refuse"

#: Probe / Floor text when import, Alembic, or sqlite DDL crashes the probe.
SCHEMA_SQL_HALT = "workspace schema or migration failed"

#: Runs inside the generated workspace. Prints one finding per line to
#: stderr and exits non-zero; a clean run exits 0 and prints nothing.
BEHAVIOUR_PROBE = r'''
import json, os, sys, tempfile

os.environ["STORAGE_PATH"] = tempfile.mkdtemp(prefix="writer-gate-")
sys.path.insert(0, os.getcwd())

findings = []
schema_misses = []
f11_misses = []
roundtrip_misses = []

# Baked in by _render_probe() from app.factory.build.block_obligations, so
# the probe can name a missing precondition without importing the factory
# (it runs inside the generated workspace, which carries no factory code).
RESOURCE_OBLIGATIONS = {}

try:
    from app.models import MODELS
    from app import store
    from app.main import app
    from fastapi.testclient import TestClient
except Exception as exc:
    sys.stderr.write(
        "GATE-FINDING: workspace does not import: %s: %s\n"
        % (type(exc).__name__, exc)
    )
    raise SystemExit(1)

if not MODELS:
    sys.stderr.write(
        "GATE-FINDING: no capabilities to probe (app.models.MODELS is empty)\n"
    )
    raise SystemExit(1)


def _ann(cls, name):
    """Declared type for a field. PEP 563 makes annotations strings."""
    raw = getattr(cls, "__annotations__", {}).get(name, "str")
    return str(raw).replace("Optional[", "").replace("]", "").strip()


def _value(cls, name):
    """A value satisfying every constraint the field itself declares.

    Mirrors the emitter's _sample_value: a type-valid but domain-invalid
    value would be rejected by the route's own guard, and the forced-failure
    phase would then pass without a block ever being called.
    Appointment-shaped fields (scheduled_time, *_date, datetime annotations)
    must not emit the word "sample" — routes and sqlite reject it.
    """
    con = getattr(cls, "CONSTRAINTS", {}).get(name, {})
    allowed = con.get("allowed_values")
    if allowed:
        return allowed[0]
    kind = _ann(cls, name)
    kind_l = kind.lower().replace("datetime.", "").replace(" ", "")
    if kind in ("int", "float") or kind_l in ("int", "float"):
        low, high = con.get("min"), con.get("max")
        if low is not None:
            return low
        if high is not None:
            return high if high < 1 else 1
        return 1
    if kind == "bool" or kind_l == "bool":
        return False
    if "email" in name.lower():
        return "sample@example.com"
    fmt = str(con.get("format") or "").lower().replace("-", "")
    n = name.lower()
    if (
        kind_l in ("datetime", "timestamp")
        or fmt in ("datetime", "timestamp", "iso8601")
        or n.endswith("_at")
        or n.endswith("_datetime")
    ):
        return "2026-09-03T10:00:00"
    if kind_l == "date" or fmt == "date" or n.endswith("_date"):
        return "2026-09-03"
    if kind_l == "time" or fmt == "time" or n.endswith("_time") or n == "time":
        return "10:00:00"
    if n == "status" or n.endswith("_status"):
        return "open"
    return "sample"


def _payload(cls):
    return {n: _value(cls, n) for n in getattr(cls, "FIELDS", [])}


def _entity_map():
    """capability_id -> store entity, from the emitted capability manifest.

    The entity is not derivable from the capability id: LotDesk's
    ``vehicle_inventory`` persists to ``vehicle``. Guessing makes the
    row-count assertion silently inert, which is the failure this gate is
    supposed to catch.
    """
    try:
        from app.jobs import CAPABILITIES
    except Exception:
        return {}
    out = {}
    for item in CAPABILITIES or []:
        if isinstance(item, dict) and item.get("id") and item.get("entity"):
            out[item["id"]] = item["entity"]
    return out


ENTITIES = _entity_map()


def _entity_of(cap_id, cls):
    return ENTITIES.get(cap_id) or getattr(cls, "ENTITY", None) or cap_id


def _rows(entity):
    """Row count, or None if the entity cannot be read.

    Returning 0 on error would make the persistence assertion pass by
    accident whenever the entity name is wrong -- the check would look
    green while never having run.
    """
    try:
        return len(store.list_all(entity))
    except Exception:
        return None


# The app runs its own migrations at startup, so the probe must enter the
# client context rather than construct it bare -- a bare TestClient skips
# lifespan, leaving a schema-less database and "no such table" for every
# capability. Explicit upgrade_head() first for workspaces whose startup
# does not own the migration. ImportError is the JSON-store fixture path
# (no Alembic). A real migration/SQL failure is a GATE-FINDING — never
# swallowed, never left as unmarked sqlite stderr.
try:
    from app.migrations import upgrade_head
except ImportError:
    upgrade_head = None
if upgrade_head is not None:
    try:
        upgrade_head()
    except Exception as exc:
        sys.stderr.write(
            "GATE-FINDING: workspace schema or migration failed: %s: %s\n"
            % (type(exc).__name__, exc)
        )
        raise SystemExit(1)

def _probe_excepthook(typ, exc, tb):
    if issubclass(typ, SystemExit):
        return sys.__excepthook__(typ, exc, tb)
    sys.stderr.write(
        "GATE-FINDING: workspace probe crashed: %s: %s\n"
        % (getattr(typ, "__name__", typ), exc)
    )
    return sys.__excepthook__(typ, exc, tb)

sys.excepthook = _probe_excepthook

# F11: record which blocks each capability actually reaches while its
# handler runs normally. A declared BLOCK_IDS entry that is never invoked is
# a build error, not a comment -- and the baseline phase is where a handler
# runs to completion, so it is the only phase that can observe the full set.
import app.dispatch as _dispatch

_real_execute = _dispatch.execute
_seen = {"cap": None, "blocks": {}}
contract_misses = []

# CONTRACT PROBE (owner's ruling R1b, 2026-09-01).
#
# F11 says "declared means invoked". This says "invoked means ACCEPTED".
#
# The baseline phase already executes the REAL vendored blocks with the
# payload the coder wrote, so the evidence was passing through this function
# and being thrown away: only the block id was recorded, never the answer. On
# the residential-lettings build (sess_6400b6c273414352, six hours after #254
# merged) every one of these came back here and none was seen:
#
#   analytics  {'error': 'metric and value required'}   <- envelope shape
#   team       'Unknown action: None'                   <- action in payload
#   workflow   'workflow unknown field(s): action'      <- action in payload
#   team       'Team access denied'                     <- precondition
#
# The build passed WRITER, passed its own gate, shipped a 216-file zip that
# booted -- and could not persist one record.
#
# Classified, never guessed: each class is decided by the block's literal
# answer plus the payload the coder actually passed.
_REFUSAL_MARKERS = (
    "required", "unknown action", "unknown field", "not found",
    "access denied", "no input files", "invalid", "missing",
)


def _classify_refusal(block_id, payload, action, answer):
    """Name the mismatch, or return None when the answer is not a refusal."""
    if not isinstance(answer, dict):
        return None
    text = str(answer.get("error") or "")
    # Block error literals use both conventions -- "Team not found" and
    # "file_not_found" are the same class of answer, and the underscored one
    # slipped every marker until a test caught it. Match on both spellings.
    low = text.lower()
    flat = low.replace("_", " ")
    if not any(m in low or m in flat for m in _REFUSAL_MARKERS):
        return None
    data = payload if isinstance(payload, dict) else {}
    inner = data.get("input") if isinstance(data.get("input"), dict) else {}

    # (a) the action travelled inside the payload instead of as the keyword
    if "unknown action" in low or "unknown field" in low:
        if "action" in data or "action" in inner:
            return (
                "%s: the action travelled inside the payload; app/dispatch.py "
                "routes payload keys into the block's record and reads the "
                "operation only from the action= keyword. Answered %r "
                "(CONTRACT: unknown action)" % (block_id, text[:90])
            )
        if action is None:
            return (
                "%s: called with no action= keyword. Answered %r "
                "(CONTRACT: unknown action)" % (block_id, text[:90])
            )

    # (b) the record is one level too deep for a block that reads it flat.
    # input_keys_read_by_block is harvested from source, not declared --
    # WORKAROUND, removal tracked in CerebrumDev.ai#256 (design:
    # Cerebrum-Blocks#90, block.json requires_inputs).
    if inner:
        try:
            import app.dispatch as _d
            reads = set(
                (_d.BLOCK_CONTRACTS.get(block_id) or {})
                .get("input_keys_read_by_block") or []
            )
        except Exception:
            reads = set()
        buried = sorted(k for k in inner if k in reads and k != "action")
        if buried and "input" not in reads:
            return (
                "%s: envelope shape -- %s sit inside 'input' but %s reads them "
                "at the top level. Answered %r (CONTRACT: envelope shape)"
                % (block_id, ", ".join(buried), block_id, text[:90])
            )

    # (c) an action that needs an id the block itself mints, called without it
    rule = RESOURCE_OBLIGATIONS.get(block_id) or {}
    if action and action in (rule.get("into") or []):
        carry = rule.get("carry")
        if carry and not data.get(carry) and not inner.get(carry):
            return (
                "%s: called %s without %s. %s mints it and must run first, "
                "with the returned %s carried in. Answered %r "
                "(CONTRACT: missing precondition)"
                % (block_id, action, carry, rule.get("ensure"), carry,
                   text[:90])
            )

    return (
        "%s: refused the payload the coder wrote -- answered %r (CONTRACT)"
        % (block_id, text[:110])
    )


def _recording_execute(block_id, *a, **kw):
    cap = _seen["cap"]
    if cap is not None:
        _seen["blocks"].setdefault(cap, set()).add(str(block_id))
    result = _real_execute(block_id, *a, **kw)
    if cap is not None:
        payload = a[0] if a else kw.get("payload")
        act = kw.get("action")
        if act is None and len(a) > 1:
            act = a[1]
        note = _classify_refusal(block_id, payload, act, result)
        if note:
            line = "%s: %s" % (cap, note)
            if line not in contract_misses:
                contract_misses.append(line)
    return result


_dispatch.execute = _recording_execute
for _name, _mod in list(sys.modules.items()):
    if _name.startswith("app.actions") and hasattr(_mod, "execute"):
        _mod.execute = _recording_execute

try:
    client_cm = TestClient(app)
    client = client_cm.__enter__()
except Exception as exc:
    sys.stderr.write(
        "GATE-FINDING: workspace does not boot: %s: %s\n"
        % (type(exc).__name__, exc)
    )
    raise SystemExit(1)
targets = []


# ONE-RECORD ROUND TRIP (owner's ruling R1e, 2026-09-01).
#
#   "Boots and passes its own tests" is no longer enough; the product must
#   remember one thing it was told.
#
# residential-lettings booted, served all its routes, passed its own gate --
# and answered every GET with {"items": [], "total": 0}. Nothing in the
# factory asked the one question a buyer asks first: I gave it a record, is
# it still there?
#
# Scope, so this bites the real defect and nothing else. Only a capability
# whose entity is READABLE is judged: store.list_all does SELECT * FROM
# <entity>, so a generate-only capability with no table raises, _rows
# returns None, and the capability is reported as unjudged rather than
# failed. A capability that persists is judged on both halves -- the store
# grew, AND the GET hands the record back.
_ROUND_TRIP_CHECKED = set()


def _record_matches(record, body):
    """Does this stored/returned record carry a value the POST supplied?"""
    if not isinstance(record, dict):
        return False
    for key, want in (body or {}).items():
        got = record.get(key)
        if got is None:
            continue
        if got == want or str(got) == str(want):
            return True
    return False


def _listed_records(payload):
    """Pull the record list out of whatever shape the list route answers."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("items", "records", "results", "data", "rows"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _check_round_trip(cap_id, cls, body):
    """POST created it; can it be read back? Judged once per capability."""
    if cap_id in _ROUND_TRIP_CHECKED:
        return
    _ROUND_TRIP_CHECKED.add(cap_id)
    entity = _entity_of(cap_id, cls)
    rows = _rows(entity)
    if rows is None:
        # Not judgeable rather than failed: no table to read.
        return
    if rows < 1:
        roundtrip_misses.append(
            "%s: POST reported success and %s holds 0 row(s) -- the product "
            "did not remember what it was told (ROUND-TRIP: nothing stored)"
            % (cap_id, entity)
        )
        return
    stored = store.list_all(entity)
    if not any(_record_matches(r, body) for r in stored):
        roundtrip_misses.append(
            "%s: %s grew to %d row(s) but none carries a value the POST "
            "supplied (ROUND-TRIP: wrong record)" % (cap_id, entity, rows)
        )
        return
    # ... and the GET the buyer actually makes.
    try:
        got = client.get("/v1/" + cap_id)
    except Exception as exc:
        findings.append(
            "%s: GET raised %s: %s" % (cap_id, type(exc).__name__, exc)
        )
        return
    if got.status_code in (404, 405):
        return  # no list route on this capability; the store half stands
    if got.status_code != 200:
        roundtrip_misses.append(
            "%s: stored the record, then GET answered HTTP %s "
            "(ROUND-TRIP: not readable)" % (cap_id, got.status_code)
        )
        return
    listed = _listed_records(got.json() if got.content else {})
    if not listed:
        roundtrip_misses.append(
            "%s: %s holds %d row(s) and GET answered with none -- the exact "
            "residential-lettings answer (ROUND-TRIP: empty list)"
            % (cap_id, entity, rows)
        )
        return
    if not any(_record_matches(r, body) for r in listed):
        roundtrip_misses.append(
            "%s: GET returned %d record(s), none carrying a value the POST "
            "supplied (ROUND-TRIP: wrong record returned)"
            % (cap_id, len(listed))
        )

# -- phase 1: baseline -----------------------------------------------------
for cap_id, cls in MODELS.items():
    body = _payload(cls)
    _seen["cap"] = cap_id
    try:
        resp = client.post("/v1/" + cap_id, json=body)
    except Exception as exc:
        schema_misses.append(
            "%s: POST raised %s: %s" % (cap_id, type(exc).__name__, exc)
        )
        continue
    if resp.status_code != 200:
        schema_misses.append(
            "%s: baseline POST returned HTTP %s" % (cap_id, resp.status_code)
        )
        continue
    data = resp.json() if resp.content else {}
    if data.get("ok") is not False:
        _check_round_trip(cap_id, cls, body)
    # House convention: ``ok is False`` is the refusal. A route that omits
    # ``ok`` has not refused, so it belongs in phase two rather than being
    # reported here as a schema rejection.
    if data.get("ok") is False:
        # Kernel wrap (#237) and the handler wrap refuse success when a
        # real block fails on the sample payload. That is not a schema
        # miss: a block was reached, so phase two can still judge
        # fail-closed. Treating it as a skip emptied targets on the live
        # construction kit and SystemExit'd before the F1 miss path.
        if _seen["blocks"].get(cap_id):
            targets.append((cap_id, cls))
            continue
        schema_misses.append(
            "%s: refused a payload built from its own declared constraints (%s)"
            % (cap_id, str(data.get("error"))[:160])
        )
        continue
    targets.append((cap_id, cls))

_seen["cap"] = None

# F11: a declared BLOCK_IDS entry that never gets invoked is a build error,
# not a comment. Checked after the baseline because that is the phase where
# a handler runs to completion -- under forced failure it may short-circuit
# on the first block and never reach the rest.
for _cap_id, _cls in targets:
    _mod = sys.modules.get("app.actions." + _cap_id.replace("-", "_"))
    _declared = [str(b) for b in (getattr(_mod, "BLOCK_IDS", None) or [])]
    if not _declared:
        continue
    _invoked = _seen["blocks"].get(_cap_id, set())
    _never = sorted(b for b in _declared if b not in _invoked)
    if _never:
        f11_misses.append(
            "%s: declares block(s) it never invokes: %s (F11)"
            % (_cap_id, ", ".join(_never))
        )

if not targets:
    # Every capability failed schema (or never reached a probeable
    # state). Isolated schema misses do not take this path. Always emit
    # the canonical sentence first so the host cannot map this halt to F1.
    sys.stderr.write(
        "GATE-FINDING: no capability accepted its own schema\n"
        + "".join("GATE-FINDING: %s\n" % f for f in findings)
        + "".join("GATE-FINDING: %s\n" % f for f in schema_misses)
    )
    raise SystemExit(1)

# -- phase 2: every block call fails --------------------------------------
import app.dispatch as _dispatch


_calls = {"n": 0}


def _forced_failure(block_id, payload=None, action=None, params=None, **kw):
    _calls["n"] += 1
    return {
        "status": "error",
        "block": block_id,
        "action": action,
        "error": "writer_behaviour gate: forced block failure",
    }


_dispatch.execute = _forced_failure
# Handlers bind the name at import time, so patch the bound references too.
for _name, _mod in list(sys.modules.items()):
    if _name.startswith("app.actions") and hasattr(_mod, "execute"):
        _mod.execute = _forced_failure

misses = []
honest = 0

for cap_id, cls in targets:
    entity = _entity_of(cap_id, cls)
    before = _rows(entity)
    body = _payload(cls)
    _calls["n"] = 0
    try:
        resp = client.post("/v1/" + cap_id, json=body)
    except Exception as exc:
        findings.append("%s: POST raised under forced failure: %s" % (cap_id, exc))
        continue
    data = resp.json() if resp.content else {}
    if _calls["n"] == 0:
        # The capability never reached a block, so there is no block failure
        # for it to propagate. A pure-GENERATE capability is legitimately in
        # this position; asserting fail-closed here would reject it for
        # having no dependency to fail.
        continue
    missed = False
    if resp.status_code == 200 and data.get("ok") is not False:
        misses.append(
            "%s: did not fail closed — answered %s while every block call "
            "failed (F1)" % (cap_id, json.dumps(data)[:120])
        )
        missed = True
    after = _rows(entity)
    if before is None or after is None:
        findings.append(
            "%s: cannot read entity %r — persistence was never checked" % (cap_id, entity)
        )
    elif after > before:
        misses.append(
            "%s: persisted %d row(s) after a failed handler (F1)" % (cap_id, after - before)
        )
        missed = True
    if not missed:
        honest += 1

if findings:
    sys.stderr.write("".join("GATE-FINDING: %s\n" % f for f in findings))
    raise SystemExit(1)
_f11_caps = set(m.split(":", 1)[0] for m in f11_misses)
if f11_misses and all(cid in _f11_caps for cid, _cls in targets):
    # Every probed capability declared unused BLOCK_IDS. Isolated F11
    # (below) continues so a mixed workspace can still ship a zip.
    sys.stderr.write(
        "GATE-FINDING: a capability declares block(s) it never invokes\n"
        + "".join("GATE-FINDING: %s\n" % f for f in f11_misses)
    )
    raise SystemExit(1)
_rt_caps = set(m.split(":", 1)[0] for m in roundtrip_misses)
if roundtrip_misses and all(cid in _rt_caps for cid, _cls in targets):
    # Nothing the product was told survived. That is residential-lettings
    # exactly, and it is the bar "boots and passes its own tests" never
    # reached. Isolated misses (below) record and continue.
    sys.stderr.write(
        "GATE-FINDING: no capability could read back a record it stored\n"
        + "".join("GATE-FINDING: %s\n" % f for f in roundtrip_misses)
    )
    raise SystemExit(1)
_contract_caps = set(m.split(":", 1)[0] for m in contract_misses)
if contract_misses and all(cid in _contract_caps for cid, _cls in targets):
    # Every probed capability wrote a payload its own blocks refuse. That is
    # the residential-lettings shape exactly: a zip that boots and cannot
    # persist. Isolated contract misses (below) continue so a mixed
    # workspace can still ship.
    sys.stderr.write(
        "GATE-FINDING: every capability wrote a payload its blocks refuse\n"
        + "".join("GATE-FINDING: %s\n" % f for f in contract_misses)
    )
    raise SystemExit(1)
if misses and honest == 0:
    # Every block-reaching capability lied. Same as LotDesk: the WRITER
    # produced nothing honest to ship. Isolated misses (below) continue.
    sys.stderr.write("".join("GATE-FINDING: %s\n" % f for f in misses))
    raise SystemExit(1)
# Isolated F1, F11, contract and schema refusals: record, do not halt.
recorded = (
    list(misses) + list(schema_misses) + list(f11_misses)
    + list(contract_misses) + list(roundtrip_misses)
)
if recorded:
    sys.stdout.write("".join("GATE-MISS: %s\n" % m for m in recorded))
'''


def _is_schema_line(line: str) -> bool:
    """True when a probe line is a schema refusal, not an F1 lie."""
    return any(
        token in line
        for token in (
            SCHEMA_HALT,
            SCHEMA_SQL_HALT,
            "workspace does not import",
            "workspace does not boot",
            "workspace probe crashed",
            "refused a payload",
            "own declared constraints",
            "baseline POST",
            "POST raised",
        )
    )


_SQL_COL_RE = re.compile(
    r"^[A-Za-z_][\w]*\s+"
    r"(TEXT|INTEGER|REAL|BLOB|NUMERIC|FLOAT|BOOLEAN|DATETIME|DATE|TIME)\b",
    re.IGNORECASE,
)


def _looks_like_sql_ddl_line(line: str) -> bool:
    """True when a stderr line is a CREATE TABLE fragment, not a reason."""
    s = line.strip().rstrip(",")
    if not s:
        return False
    upper = s.upper()
    if upper.startswith("CREATE TABLE") or upper.startswith("PRIMARY KEY"):
        return True
    if s in {")", "]", ");"} or s.startswith("[SQL"):
        return True
    return _SQL_COL_RE.match(s) is not None


def classify_unmarked_probe_failure(raw_lines: list[str]) -> str:
    """Turn unmarked sqlite/alembic stderr into one GATE-FINDING sentence.

    Live veterinary-care (sess_3daeca83ae9d4286): the probe crashed during
    import/migration, SQLAlchemy dumped the CREATE TABLE body to stderr,
    and the host took ``lines = raw[-8:]`` so the Floor banner was the
    first column line (``scheduled_time TEXT,``) instead of a reason.
    """
    nonempty = [ln.strip() for ln in raw_lines if ln.strip()]
    for ln in nonempty:
        if any(
            tok in ln
            for tok in (
                "OperationalError",
                "IntegrityError",
                "ProgrammingError",
                "CompileError",
                "StatementError",
            )
        ):
            msg = ln.split("[SQL:", 1)[0].strip()
            if msg and not _looks_like_sql_ddl_line(msg):
                return f"{SCHEMA_SQL_HALT}: {msg[:240]}"
    text = "\n".join(nonempty)
    if any(
        tok in text
        for tok in (
            "CREATE TABLE",
            "PRIMARY KEY",
            "sqlite3",
            "alembic",
            "OperationalError",
        )
    ):
        return (
            f"{SCHEMA_SQL_HALT}: sqlite/alembic printed DDL without a "
            "GATE-FINDING (probe crashed during import or migration)"
        )
    last = next(
        (ln for ln in reversed(nonempty) if not _looks_like_sql_ddl_line(ln)),
        "",
    )
    if last:
        return f"workspace probe crashed: {last[:240]}"
    return "behaviour probe failed with no output"


def findings_from_probe_stderr(stderr: str) -> list[str]:
    """Marked findings, or one classified reason — never raw SQL lines."""
    raw = (stderr or "").splitlines()
    marked = [
        ln.split("GATE-FINDING: ", 1)[1] for ln in raw if "GATE-FINDING: " in ln
    ]
    if marked:
        return marked[-20:]
    return [classify_unmarked_probe_failure(raw)]


def _is_f11_line(line: str) -> bool:
    """True when a probe line is unused BLOCK_IDS (F11), not an F1 lie.

    ``(F1)`` is a substring of ``(F11)``, so F1 matching must not run
    first or an F11 finding becomes the LotDesk banner.
    """
    return any(
        token in line
        for token in (F11_HALT, "(F11)", "never invokes", "declares block(s)")
    )


def _is_round_trip_line(line: str) -> bool:
    """True when a probe line is a record that did not survive.

    Its own class again: the route accepted the payload, the blocks may all
    have answered fine, and the handler may have failed closed correctly --
    and the product still forgot what it was told.
    """
    return "(ROUND-TRIP" in line


def _is_contract_line(line: str) -> bool:
    """True when a probe line is a block refusing the coder's payload.

    Its own class: not a schema refusal (the ROUTE accepted the payload),
    not F11 (the block WAS invoked), not F1 (the handler did fail closed --
    that is how the refusal surfaced at all).
    """
    return "(CONTRACT" in line


def _is_f1_line(line: str) -> bool:
    """True when a probe line is success-over-failed-block, not F11."""
    if _is_f11_line(line):
        return False
    return "(F1)" in line or "did not fail closed" in line or "persisted" in line


def banner_detail(findings: list[str]) -> str:
    """Floor banner text from probe findings.

    Live construction (2026-08-30): findings were
    ``no capability accepted its own schema`` but the host mapped every
    non-zero exit onto F1, so the Floor lied about which phase failed.
    The same mapping turned F11 unused-block findings into F1 because
    ``(F1)`` is a substring of ``(F11)``.

    Live veterinary-care (2026-09-03): unmarked sqlite DDL
    (``scheduled_time TEXT,``) must never become the banner.
    """
    usable = [ln for ln in findings if ln and not _looks_like_sql_ddl_line(ln)]
    if not findings:
        return "behaviour probe failed with no output"
    if not usable:
        return classify_unmarked_probe_failure(findings)
    if any(SCHEMA_HALT in ln for ln in usable):
        return SCHEMA_HALT
    if any(SCHEMA_SQL_HALT in ln for ln in usable):
        return next(ln for ln in usable if SCHEMA_SQL_HALT in ln)
    if any(_is_schema_line(ln) for ln in usable):
        return next(ln for ln in usable if _is_schema_line(ln))
    if any(F11_HALT in ln or _is_f11_line(ln) for ln in usable):
        return F11_HALT
    if any(CONTRACT_HALT in ln or _is_contract_line(ln) for ln in usable):
        return CONTRACT_HALT
    if any(_is_f1_line(ln) for ln in usable):
        return F1_HALT
    return usable[0]


def _pass_detail(
    f1_misses: list[str], schema_misses: list[str], f11_misses: list[str]
) -> str:
    """Success-path banner: name schema vs F11 vs F1, never collapse them."""
    parts = []
    if f1_misses:
        n = len({m.split(":", 1)[0] for m in f1_misses})
        parts.append(
            f"{n} capability(ies) recorded as misses "
            "(success over a failed block)"
        )
    if schema_misses:
        n = len({m.split(":", 1)[0] for m in schema_misses})
        parts.append(
            f"{n} capability(ies) recorded as misses (refused their own schema)"
        )
    if f11_misses:
        n = len({m.split(":", 1)[0] for m in f11_misses})
        parts.append(
            f"{n} capability(ies) recorded as misses "
            "(declared blocks they never invoke — F11)"
        )
    if parts:
        return "; ".join(parts) + "; remaining capabilities fail closed"
    return "every capability fails closed when its blocks fail"


def _render_probe() -> str:
    """The probe with this factory's resource obligations baked in."""
    from app.factory.build.block_obligations import RESOURCE_OBLIGATIONS

    return BEHAVIOUR_PROBE.replace(
        "RESOURCE_OBLIGATIONS = {}",
        "RESOURCE_OBLIGATIONS = " + repr(dict(RESOURCE_OBLIGATIONS)),
        1,
    )


def gate_writer_behaviour(ctx: "GateContext") -> "GateResult":
    """WRITER: isolated schema, F11, or F1 is a miss, not a halt.

    The probe still fail-closes when every capability is dishonest
    (all refuse their own schema, every capability declares unused
    blocks, or every block-reaching capability lies — LotDesk). One
    miss among honest ones is recorded and the phase continues so
    TESTER/STORE_MANAGER can still produce a zip. The Floor banner
    uses the actual finding (schema vs F11 vs F1).
    """
    from app.factory.build.gates import GateResult

    if not (ctx.workspace / "app" / "models.py").is_file():
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            detail="app/models.py is missing — nothing to probe",
            findings=["writer produced no models"],
        )

    proc = ctx.run([sys.executable, "-c", _render_probe()])
    if proc.returncode != 0:
        # Marked findings only. Unmarked sqlite/alembic stderr used to
        # become the Floor banner (``scheduled_time TEXT,``). Classify
        # that crash; do not hide a real schema bug by swallowing it.
        findings = findings_from_probe_stderr(proc.stderr or "")
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            detail=banner_detail(findings),
            findings=findings,
        )
    raw_out = proc.stdout or ""
    raw_err = proc.stderr or ""
    misses = [
        ln.split("GATE-MISS: ", 1)[1]
        for ln in (raw_out + "\n" + raw_err).splitlines()
        if "GATE-MISS: " in ln
    ]
    schema_misses = [m for m in misses if _is_schema_line(m)]
    f11_misses = [m for m in misses if _is_f11_line(m)]
    contract_misses = [m for m in misses if _is_contract_line(m)]
    roundtrip_misses = [m for m in misses if _is_round_trip_line(m)]
    f1_misses = [
        m for m in misses
        if not _is_schema_line(m)
        and not _is_f11_line(m)
        and not _is_contract_line(m)
        and not _is_round_trip_line(m)
    ]
    skipped = [
        ln
        for ln in raw_out.splitlines()
        if ln.strip() and ln.strip() != "SKIPPED" and "GATE-MISS: " not in ln
    ]
    detail = _pass_detail(f1_misses, schema_misses, f11_misses)
    if contract_misses:
        detail += (
            f"; {len(contract_misses)} capability(ies) wrote a payload their "
            "blocks refuse (CONTRACT)"
        )
    if roundtrip_misses:
        detail += (
            f"; {len(roundtrip_misses)} capability(ies) could not read back a "
            "record they stored (ROUND-TRIP)"
        )
    if skipped:
        detail += f"; {len(skipped)} capability(ies) skipped — see payload"
    return GateResult(
        ok=True,
        gate=GATE_NAME,
        detail=detail,
        findings=list(misses),
        payload={
            "skipped": skipped,
            "misses": misses,
            "schema_misses": schema_misses,
            "f11_misses": f11_misses,
            "f1_misses": f1_misses,
            "contract_misses": contract_misses,
            "roundtrip_misses": roundtrip_misses,
        },
    )
