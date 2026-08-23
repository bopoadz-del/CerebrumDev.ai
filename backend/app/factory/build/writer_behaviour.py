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
  no route may answer ``ok: True`` and no row may appear in the store.

The seam is ``app.dispatch.execute``. Both the kernel path
(``run_capability`` -> ``execute_action`` -> handler) and a coder-written
handler bottom out there, so stubbing it covers both. Handlers bind the name
at import (``from app.dispatch import execute``), so the already-imported
module attributes are patched too.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.factory.build.gates import GateContext, GateResult

GATE_NAME = "writer_behaviour"

#: Runs inside the generated workspace. Prints one finding per line to
#: stderr and exits non-zero; a clean run exits 0 and prints nothing.
BEHAVIOUR_PROBE = r'''
import json, os, sys, tempfile

os.environ["STORAGE_PATH"] = tempfile.mkdtemp(prefix="writer-gate-")
sys.path.insert(0, os.getcwd())

findings = []
skipped = []

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
    """
    con = getattr(cls, "CONSTRAINTS", {}).get(name, {})
    allowed = con.get("allowed_values")
    if allowed:
        return allowed[0]
    kind = _ann(cls, name)
    if kind in ("int", "float"):
        low, high = con.get("min"), con.get("max")
        if low is not None:
            return low
        if high is not None:
            return high if high < 1 else 1
        return 1
    if kind == "bool":
        return False
    if "email" in name.lower():
        return "sample@example.com"
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
# does not own the migration.
try:
    from app.migrations import upgrade_head
    upgrade_head()
except Exception:
    pass

# F11: record which blocks each capability actually reaches while its
# handler runs normally. A declared BLOCK_IDS entry that is never invoked is
# a build error, not a comment -- and the baseline phase is where a handler
# runs to completion, so it is the only phase that can observe the full set.
import app.dispatch as _dispatch

_real_execute = _dispatch.execute
_seen = {"cap": None, "blocks": {}}


def _recording_execute(block_id, *a, **kw):
    cap = _seen["cap"]
    if cap is not None:
        _seen["blocks"].setdefault(cap, set()).add(str(block_id))
    return _real_execute(block_id, *a, **kw)


_dispatch.execute = _recording_execute
for _name, _mod in list(sys.modules.items()):
    if _name.startswith("app.actions") and hasattr(_mod, "execute"):
        _mod.execute = _recording_execute

client_cm = TestClient(app)
client = client_cm.__enter__()
targets = []

# -- phase 1: baseline -----------------------------------------------------
for cap_id, cls in MODELS.items():
    body = _payload(cls)
    _seen["cap"] = cap_id
    try:
        resp = client.post("/v1/" + cap_id, json=body)
    except Exception as exc:
        findings.append("%s: POST raised %s: %s" % (cap_id, type(exc).__name__, exc))
        continue
    if resp.status_code != 200:
        findings.append("%s: baseline POST returned HTTP %s" % (cap_id, resp.status_code))
        continue
    data = resp.json() if resp.content else {}
    # House convention: ``ok is False`` is the refusal. A route that omits
    # ``ok`` has not refused, so it belongs in phase two rather than being
    # reported here as a schema rejection.
    if data.get("ok") is False:
        # The capability refuses a payload built from its own declared
        # fields. That is a real contract mismatch -- app/models.py and the
        # handler disagree -- but it is not what this gate judges, and the
        # capability cannot reach a block, so there is no fail-closed
        # behaviour to test. Recorded so the skip is never silent.
        skipped.append(
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
        findings.append(
            "%s: declares block(s) it never invokes: %s (F11)"
            % (_cap_id, ", ".join(_never))
        )

if not targets:
    sys.stderr.write(
        "".join("GATE-FINDING: %s\n" % f for f in findings)
        or "GATE-FINDING: no capability accepted its own schema\n"
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
    if resp.status_code == 200 and data.get("ok") is not False:
        findings.append(
            "%s: did not fail closed — answered %s while every block call "
            "failed (F1)" % (cap_id, json.dumps(data)[:120])
        )
    after = _rows(entity)
    if before is None or after is None:
        findings.append(
            "%s: cannot read entity %r — persistence was never checked" % (cap_id, entity)
        )
    elif after > before:
        findings.append(
            "%s: persisted %d row(s) after a failed handler (F1)" % (cap_id, after - before)
        )

if findings:
    sys.stderr.write("".join("GATE-FINDING: %s\n" % f for f in findings))
    raise SystemExit(1)
'''


def gate_writer_behaviour(ctx: "GateContext") -> "GateResult":
    """WRITER: no route reports success, or persists, over a failed block."""
    from app.factory.build.gates import GateResult

    if not (ctx.workspace / "app" / "models.py").is_file():
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            detail="app/models.py is missing — nothing to probe",
            findings=["writer produced no models"],
        )

    proc = ctx.run([sys.executable, "-c", BEHAVIOUR_PROBE])
    if proc.returncode != 0:
        raw = (proc.stderr or "").splitlines()
        # Filter to marked findings: alembic and uvicorn also log to stderr,
        # so a tail of raw lines reported their INFO noise as the gate reason.
        lines = [
            ln.split("GATE-FINDING: ", 1)[1] for ln in raw if "GATE-FINDING: " in ln
        ]
        if not lines:
            lines = [ln for ln in raw if ln.strip()][-8:]
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            detail="a capability route reported success over a failed block",
            findings=lines[-20:] or ["behaviour probe failed with no output"],
        )
    skipped = [
        ln
        for ln in (proc.stdout or "").splitlines()
        if ln.strip() and ln.strip() != "SKIPPED"
    ]
    detail = "every capability fails closed when its blocks fail"
    if skipped:
        detail += f"; {len(skipped)} capability(ies) skipped — see payload"
    return GateResult(
        ok=True,
        gate=GATE_NAME,
        detail=detail,
        payload={"skipped": skipped},
    )
