"""PRODUCT gate: does the thing that shipped actually work? (ruling 1, 2026-09-01)

FINDING 3, in the owner's words:

    the factory gate must not be claimable as "all phase gates passed" while
    the only tests that check a business action are excluded. Add a third
    phase gate, PRODUCT: runs post-boot, executes the pilot-marked tests
    against the booted product AND the R1e one-record round-trip per
    capability. Code-phase gate stays as is; the verdict line becomes three
    gates, each named with its scope. A product that passes code-phase but
    fails PRODUCT is reported exactly so.

The defect this closes is not a bug in any one check; it is a claim. The
code-phase suite runs ``pytest -m "not pilot"`` -- and ``@pytest.mark.pilot``
is precisely the marker on the tests that exercise a business action against
a booted product. So "all phase gates passed" was, literally, "everything
except the tests that check the product works passed". residential-lettings
built, shipped a 216-file zip, booted, served all seventeen routes, and
could not persist one record -- with every gate green.

THE TWO HALVES, and why both are needed:

* the pilot-marked SUITE is what the TESTER wrote about this product's own
  behaviour. It is the product's own account of itself.
* the one-record ROUND-TRIP (R1e) is the factory's account, identical for
  every product and impossible to write around: POST creates, GET returns
  it. A suite can be green and shallow; a round-trip cannot.

SCOPE, stated so a reader knows what a PASS here does and does not mean.
This gate boots the product in-process (``TestClient``, so the lifespan runs
its migrations and R1c preconditions) and asks each capability to remember
one record. It is not a deployment, not a load test, and not a judgement of
whether the answers are correct -- only that the product is a product.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.factory.build.gates import GateContext, GateResult

GATE_NAME = "product_green"

#: Scope sentences for the three-gate verdict line. Each says what the gate
#: LOOKED AT, so no one has to infer it from a gate name again.
GATE_SCOPES = {
    "CODE": 'the code-phase suite (pytest -m "not pilot") — imports, routes, handlers',
    "PRODUCT": (
        "post-boot: the pilot-marked tests against the booted product, and a "
        "one-record round-trip per capability (POST creates, GET returns it)"
    ),
    "STORE": "publish authorisation and outcome durability across a restart",
}

#: Boots the product and asks every capability to remember one record.
#:
#: Runs inside the GENERATED workspace, which carries no factory code, so it
#: is a source string rather than an import. Findings are marked so the
#: gate never mistakes alembic's or uvicorn's stderr for its own reason.
ROUND_TRIP_PROBE = r'''
import sys

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
        "GATE-FINDING: no capabilities to round-trip (app.models.MODELS is empty)\n"
    )
    raise SystemExit(1)


def _ann(cls, name):
    raw = getattr(cls, "__annotations__", {}).get(name, "str")
    return str(raw).replace("Optional[", "").replace("]", "").strip()


def _value(cls, name):
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
    try:
        return len(store.list_all(entity))
    except Exception:
        return None


def _record_matches(record, body):
    if not isinstance(record, dict):
        return False
    for key, want in (body or {}).items():
        got = record.get(key)
        if got is None:
            continue
        if got == want or str(got) == str(want):
            return True
    return False


def _listed(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("items", "records", "results", "data", "rows"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


# The lifespan is the point: it runs the migrations and, since R1c, the
# platform preconditions. A bare TestClient() skips it, and every capability
# would then fail on a schema-less database for a reason that has nothing to
# do with the product.
client_cm = TestClient(app)
client = client_cm.__enter__()

misses = []
passed = []
unjudged = []

for cap_id, cls in MODELS.items():
    body = _payload(cls)
    try:
        resp = client.post("/v1/" + cap_id, json=body)
    except Exception as exc:
        misses.append("%s: POST raised %s: %s" % (cap_id, type(exc).__name__, exc))
        continue
    if resp.status_code != 200:
        misses.append("%s: POST answered HTTP %s" % (cap_id, resp.status_code))
        continue
    data = resp.json() if resp.content else {}
    if isinstance(data, dict) and data.get("ok") is False:
        misses.append(
            "%s: POST refused its own sample payload: %s"
            % (cap_id, str(data.get("error") or data)[:120])
        )
        continue

    entity = _entity_of(cap_id, cls)
    rows = _rows(entity)
    if rows is None:
        # No readable entity: a generate-only capability has nothing to
        # remember. Named rather than counted as a pass, so a product made
        # entirely of these cannot be reported as round-tripping.
        unjudged.append("%s (no readable entity %r)" % (cap_id, entity))
        continue
    if rows < 1:
        misses.append(
            "%s: POST reported success and %s holds 0 row(s) -- the product "
            "did not remember what it was told" % (cap_id, entity)
        )
        continue
    if not any(_record_matches(r, body) for r in store.list_all(entity)):
        misses.append(
            "%s: %s grew to %d row(s) but none carries a value the POST "
            "supplied" % (cap_id, entity, rows)
        )
        continue

    try:
        got = client.get("/v1/" + cap_id)
    except Exception as exc:
        misses.append("%s: GET raised %s: %s" % (cap_id, type(exc).__name__, exc))
        continue
    if got.status_code in (404, 405):
        # No list route. The store half stands and is reported as such.
        passed.append("%s (stored; no list route to read it back)" % cap_id)
        continue
    if got.status_code != 200:
        misses.append(
            "%s: stored the record, then GET answered HTTP %s"
            % (cap_id, got.status_code)
        )
        continue
    listed = _listed(got.json() if got.content else {})
    if not listed:
        misses.append(
            "%s: %s holds %d row(s) and GET answered with none"
            % (cap_id, entity, rows)
        )
        continue
    if not any(_record_matches(r, body) for r in listed):
        misses.append(
            "%s: GET returned %d record(s), none carrying a value the POST "
            "supplied" % (cap_id, len(listed))
        )
        continue
    passed.append(cap_id)

for m in misses:
    sys.stdout.write("GATE-MISS: %s\n" % m)
for u in unjudged:
    sys.stdout.write("GATE-UNJUDGED: %s\n" % u)
sys.stdout.write(
    "PRODUCT-SUMMARY: %d round-tripped, %d failed, %d unjudged, %d capabilities\n"
    % (len(passed), len(misses), len(unjudged), len(MODELS))
)
raise SystemExit(1 if misses else 0)
'''


def _marked(lines: List[str], prefix: str) -> List[str]:
    return [ln.split(prefix, 1)[1].strip() for ln in lines if prefix in ln]


def gate_round_trip(ctx: "GateContext") -> "GateResult":
    """Every capability remembers one record it was given.

    Fails when ANY capability fails to round-trip, and also when NOTHING was
    judgeable: a gate that judged nothing must not report a pass, which is
    the same class of defect as the excluded pilot tests.
    """
    from app.factory.build.gates import GateResult

    if not (ctx.workspace / "app" / "models.py").is_file():
        return GateResult(
            ok=False,
            gate="product_round_trip",
            detail="app/models.py is missing — there is no product to boot",
            findings=["no models to round-trip"],
        )

    proc = ctx.run([sys.executable, "-c", ROUND_TRIP_PROBE])
    out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).splitlines()
    findings = _marked(out, "GATE-FINDING: ")
    misses = _marked(out, "GATE-MISS: ")
    unjudged = _marked(out, "GATE-UNJUDGED: ")
    summary = next(
        (ln.split("PRODUCT-SUMMARY: ", 1)[1].strip()
         for ln in out if "PRODUCT-SUMMARY: " in ln),
        "",
    )

    if findings:
        return GateResult(
            ok=False,
            gate="product_round_trip",
            detail="the product did not boot: " + findings[0],
            findings=findings[:20],
        )
    if misses:
        return GateResult(
            ok=False,
            gate="product_round_trip",
            detail=(
                "%d capability(ies) did not remember a record they were given"
                % len(misses)
            ),
            findings=misses[:20],
            payload={"summary": summary, "unjudged": unjudged},
        )
    if proc.returncode != 0:
        return GateResult(
            ok=False,
            gate="product_round_trip",
            detail="round-trip probe exited %s with no finding" % proc.returncode,
            findings=[ln for ln in out if ln.strip()][-8:] or ["no output"],
        )
    if summary.startswith("0 round-tripped"):
        return GateResult(
            ok=False,
            gate="product_round_trip",
            detail=(
                "no capability was judgeable — the round-trip check ran and "
                "decided nothing, which is not a pass"
            ),
            findings=unjudged[:20] or ["every capability was unjudged"],
            payload={"summary": summary},
        )
    return GateResult(
        ok=True,
        gate="product_round_trip",
        detail=summary or "every capability round-tripped a record",
        payload={"summary": summary, "unjudged": unjudged},
    )


def gate_product(ctx: "GateContext") -> "GateResult":
    """PRODUCT: the pilot-marked suite AND the one-record round-trip.

    Both halves must pass, and a failure names WHICH half — "PRODUCT failed"
    with no scope is the shape of report this gate exists to replace.
    """
    from app.factory.build.gates import GateResult, gate_suite_green
    from dataclasses import replace

    suite = gate_suite_green(replace(ctx, suite_marker="pilot"))
    if not suite.ok:
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            detail="PRODUCT (pilot-marked suite): " + suite.detail,
            findings=list(suite.findings),
            payload={"half": "pilot_suite", **dict(suite.payload)},
        )

    trip = gate_round_trip(ctx)
    if not trip.ok:
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            detail="PRODUCT (one-record round-trip): " + trip.detail,
            findings=list(trip.findings),
            payload={"half": "round_trip", **dict(trip.payload)},
        )
    return GateResult(
        ok=True,
        gate=GATE_NAME,
        detail="PRODUCT: %s; round-trip: %s" % (suite.detail, trip.detail),
        payload={"pilot_suite": suite.detail, **dict(trip.payload)},
    )
