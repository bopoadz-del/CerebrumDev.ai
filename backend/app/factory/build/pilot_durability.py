"""Pilot gate: a written record must survive the process that wrote it.

``pilot_ready`` is read off a ``RUN_SUCCEEDED`` event whose cycle is
``pilot``, so it means "the pilot cycle's phases passed". Every one of those
phases runs in a single interpreter: the suite writes and reads back through
the same import, the same module globals and, for an in-process block, the
same object graph. A platform whose persistence never leaves memory passes
all of it.

That is the demo/pilot boundary. A pilot writes a customer's data on Monday
and is expected to still have it on Tuesday, across a redeploy. This gate
asserts the smallest honest version of that: write in one process, read in
another, with nothing shared but the storage path.

The second process is a real ``sys.executable`` child rather than a
reimport, because a reimport keeps module-level caches alive -- which is
exactly the failure mode being tested. LotDesk's queue reported
``enqueued: True`` and then ``pending: 0``, because the block instance
holding the deque was discarded with the call.

Runs only on the pilot cycle. The code cycle is a 20-30 minute coder pass
and is not where durability is decided.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.factory.build.gates import GateContext, GateResult

GATE_NAME = "pilot_outcome_survives_restart"

#: Writes in this process, then re-reads from a fresh interpreter that
#: shares only STORAGE_PATH. Findings to stderr, non-zero exit on failure.
DURABILITY_PROBE = r'''
import json, os, subprocess, sys, tempfile

STORAGE = tempfile.mkdtemp(prefix="pilot-durability-")
os.environ["STORAGE_PATH"] = STORAGE
sys.path.insert(0, os.getcwd())

findings = []

try:
    from app.models import MODELS
    from app.main import app
    from fastapi.testclient import TestClient
except Exception as exc:
    sys.stderr.write("workspace does not import: %s: %s\n" % (type(exc).__name__, exc))
    raise SystemExit(1)

try:
    from app.migrations import upgrade_head
    upgrade_head()
except Exception:
    pass


def _ann(cls, name):
    raw = getattr(cls, "__annotations__", {}).get(name, "str")
    return str(raw).replace("Optional[", "").replace("]", "").strip()


def _value(cls, name):
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


def _entities():
    try:
        from app.jobs import CAPABILITIES
    except Exception:
        return {}
    return {
        i["id"]: i["entity"]
        for i in (CAPABILITIES or [])
        if isinstance(i, dict) and i.get("id") and i.get("entity")
    }


ENTITIES = _entities()
written = {}

# -- process one: write ----------------------------------------------------
client_cm = TestClient(app)
client = client_cm.__enter__()
for cap_id, cls in MODELS.items():
    entity = ENTITIES.get(cap_id, cap_id)
    resp = client.post("/v1/" + cap_id, json=_payload(cls))
    data = resp.json() if resp.content else {}
    if resp.status_code != 200 or data.get("ok") is False:
        # Not this gate's finding: the writer gate judges acceptance.
        continue
    written[cap_id] = entity
try:
    client_cm.__exit__(None, None, None)
except Exception:
    pass

if not written:
    sys.stderr.write("no capability accepted a write; durability not provable\n")
    raise SystemExit(1)

# -- process two: a genuinely new interpreter ------------------------------
READBACK = (
    "import json, os, sys\n"
    "sys.path.insert(0, os.getcwd())\n"
    "from app import store\n"
    "out = {}\n"
    "for cap, ent in json.loads(os.environ['PILOT_ENTITIES']).items():\n"
    "    try:\n"
    "        out[cap] = len(store.list_all(ent))\n"
    "    except Exception as exc:\n"
    "        out[cap] = 'ERROR: ' + type(exc).__name__ + ': ' + str(exc)\n"
    "sys.stdout.write(json.dumps(out))\n"
)

env = dict(os.environ)
env["STORAGE_PATH"] = STORAGE
env["PILOT_ENTITIES"] = json.dumps(written)
env["PYTHONUTF8"] = "1"
env["PYTHONIOENCODING"] = "utf-8"

proc = subprocess.run(
    [sys.executable, "-c", READBACK],
    cwd=os.getcwd(),
    capture_output=True,
    text=True,
    env=env,
    timeout=300,
)

if proc.returncode != 0:
    sys.stderr.write(
        "the second process could not read the store: %s\n"
        % ((proc.stderr or "").strip()[-400:])
    )
    raise SystemExit(1)

try:
    counts = json.loads(proc.stdout or "{}")
except ValueError:
    sys.stderr.write("unreadable read-back output: %s\n" % (proc.stdout or "")[:200])
    raise SystemExit(1)

for cap_id, entity in written.items():
    seen = counts.get(cap_id)
    if isinstance(seen, str):
        findings.append("%s: %s cannot be read from a new process (%s)" % (cap_id, entity, seen))
    elif not isinstance(seen, int) or seen < 1:
        findings.append(
            "%s: wrote to %s and a new process sees %r row(s) — persistence "
            "did not outlive the writing process" % (cap_id, entity, seen)
        )

if findings:
    sys.stderr.write("\n".join(findings) + "\n")
    raise SystemExit(1)
'''


def gate_pilot_outcome_survives_restart(ctx: "GateContext") -> "GateResult":
    """A record written by one process is readable by the next."""
    from app.factory.build.gates import GateResult

    if str(getattr(ctx, "cycle", "code")).lower() != "pilot":
        return GateResult(
            ok=True,
            gate=GATE_NAME,
            detail="code cycle — durability is decided on the pilot cycle",
        )

    if not (ctx.workspace / "app" / "models.py").is_file():
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            detail="app/models.py is missing — nothing to persist",
            findings=["no models to probe"],
        )

    proc = ctx.run([sys.executable, "-c", DURABILITY_PROBE])
    if proc.returncode != 0:
        lines = [ln for ln in (proc.stderr or "").splitlines() if ln.strip()]
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            detail="a written record did not survive the process that wrote it",
            findings=lines[-20:] or ["durability probe failed with no output"],
        )
    return GateResult(
        ok=True,
        gate=GATE_NAME,
        detail="written records are readable from a separate process",
    )
