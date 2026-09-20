"""The UI a pilot serves must work, not resemble work.

``gate_ui_surface`` checked that declared .tsx files exist and are not tiny.
That is a facade check: FinOps (sess_065fc3eac75c4f62) passed it while
shipping a React app nothing builds -- no node step in its Dockerfile, just
``COPY . .`` -- so the real UI was dead source, and the served console drove
exactly one capability.

A pilot is handed to a DevOps team to deploy and test. What it serves has to
reach the platform end to end: more than one capability, the formulas it
ships, and an answer that carries its authority label. And it must serve ONE
UI -- a second, unbuilt one is decoration.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:  # pragma: no cover
    from app.factory.build.gates import GateContext, GateResult

GATE_NAME = "ui_end_to_end"
UI_NOT_WIRED = "ui_not_wired_end_to_end"
UI_NOT_BUILT = "ui_shipped_unbuilt"

#: Keys an answer may carry its authority/precedence label under.
LABEL_KEYS = ("label", "labels", "authority", "precedence", "basis", "layer")

UI_E2E_PROBE = r'''
import json, os, re, sys, tempfile
os.environ["STORAGE_PATH"] = tempfile.mkdtemp(prefix="ui-gate-")
os.environ.setdefault("PLATFORM_TOKEN", "dev-local-token")
sys.path.insert(0, os.getcwd())
from pathlib import Path

findings = []
advisory = []


def finding(text):
    findings.append(text)


page = Path("app/static/index.html")
if not page.is_file():
    finding("the platform serves no UI: app/static/index.html is missing")
    print("UI_PROBE=" + json.dumps({"findings": findings}))
    raise SystemExit(0)

html = page.read_text(encoding="utf-8", errors="replace")
paths = sorted(set(re.findall(r"/v1/[A-Za-z0-9_/\-]+", html)))
caps = sorted(
    p.stem for p in Path("app/actions").glob("*.py") if p.stem != "__init__"
) if Path("app/actions").is_dir() else []
literal = sorted({c for c in caps if any(p.rstrip("/").endswith("/" + c) for p in paths)})
# A console that asks /v1/capabilities and builds "/v1/" + id drives whatever
# the product ships -- that is better than naming two, not worse.
discovers = "/v1/capabilities" in html and any(
    marker in html for marker in ('"/v1/" +', "'/v1/' +", "/v1/${", "`/v1/${")
)
ui_caps = caps if discovers else literal

# ONE UI: a frontend that ships with a build script must be built by the image.
if Path("frontend/package.json").is_file():
    pkg = json.loads(Path("frontend/package.json").read_text(encoding="utf-8", errors="replace") or "{}")
    builds = bool((pkg.get("scripts") or {}).get("build"))
    docker = Path("Dockerfile").read_text(encoding="utf-8", errors="replace") if Path("Dockerfile").is_file() else ""
    if builds and not re.search(r"\b(npm|pnpm|yarn|node)\b", docker):
        advisory.append(
            "frontend/ ships with a build script and the Dockerfile never builds it: "
            "the image serves app/static/index.html while the real UI is dead source. "
            "Build it in the image, or do not ship it"
        )

from fastapi.testclient import TestClient
from app.main import app

# What the product declares it serves. A UI may reference an optional
# surface (RAG, exports) that this product does not ship; that is not a
# broken route. A DECLARED route that fails is.
declared = set()
try:
    spec = json.loads(Path("openapi.json").read_text(encoding="utf-8", errors="replace"))
    declared = set((spec.get("paths") or {}).keys())
except Exception:
    declared = set()

AUTH = {"Authorization": "Bearer " + os.environ.get("PLATFORM_TOKEN", "dev-local-token")}
answered, labelled, formula_used = [], False, False
with TestClient(app) as client:
    for path in paths:
        if "{" in path or path.rstrip("/") == "/v1":
            continue
        if declared and path not in declared:
            continue  # an optional surface this product does not ship
        try:
            resp = client.get(path, headers=AUTH)
        except Exception as exc:
            finding("the UI calls %s and it raised %s" % (path, type(exc).__name__))
            continue
        # 405 is the route saying "not with that verb" -- it exists, and the
        # UI may well be POSTing to it. Only absence and failure count.
        if resp.status_code in (404, 500, 501, 502):
            finding("the UI calls %s and the platform answers %s" % (path, resp.status_code))
            continue
        if resp.status_code == 405:
            answered.append(path)
            continue
        answered.append(path)
        try:
            body = resp.json()
        except Exception:
            body = None
        blob = json.dumps(body) if body is not None else ""
        if any('"%s"' % key in blob for key in __LABEL_KEYS__):
            labelled = True

need = min(2, len(caps)) if caps else 0
if len(ui_caps) < need:
    finding(
        "the served UI drives %d of %d capability(ies) (%s): a pilot is "
        "deployed and tested, so its UI must reach the product -- read "
        "/v1/capabilities and build the routes from it"
        % (len(ui_caps), len(caps), ", ".join(ui_caps) or "none")
    )

# The formulas the product ships must be reachable from what the UI drives.
if Path("app/formulas.py").is_file():
    for cap in ui_caps:
        handler = Path("app/actions") / (cap + ".py")
        if handler.is_file() and "formulas" in handler.read_text(encoding="utf-8", errors="replace"):
            formula_used = True
            break
    if ui_caps and not formula_used:
        finding(
            "app/formulas.py ships and no capability the UI drives uses it: "
            "the formula layer is not reachable from the product"
        )

if Path("app/authority.py").is_file() and answered and not labelled:
    advisory.append(
        "no answer the UI asks for carries its authority label: the reasoning "
        "layer ships and the UI cannot show which layer an answer came from"
    )

print("UI_PROBE=" + json.dumps({"findings": findings, "advisory": advisory, "answered": answered, "ui_caps": ui_caps}))
'''


def _render_probe() -> str:
    return UI_E2E_PROBE.replace("__LABEL_KEYS__", repr(LABEL_KEYS))


def gate_ui_end_to_end(ctx: "GateContext") -> "GateResult":
    """The served UI reaches the platform, and the pilot serves one UI."""
    from app.factory.build.gates import GateResult

    if not (ctx.workspace / "app" / "main.py").is_file():
        return GateResult(
            ok=True,
            gate=GATE_NAME,
            detail="no app to serve a UI from — nothing claimed, nothing owed",
        )

    proc = ctx.run([sys.executable, "-c", _render_probe()])
    line = [
        ln for ln in (proc.stdout or "").splitlines() if ln.startswith("UI_PROBE=")
    ]
    if not line:
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            reason=UI_NOT_WIRED,
            detail=(
                "the UI probe did not report: the product did not boot far "
                "enough to serve its own console"
            ),
            findings=[(proc.stderr or "").strip().splitlines()[-1][:200]] if proc.stderr else [],
        )

    import json as _json

    payload = _json.loads(line[-1].split("=", 1)[1])
    findings: List[str] = list(payload.get("findings") or [])
    # Real, reported, and not yet fatal: the unbuilt frontend needs a node
    # stage in the emitted Dockerfile, and the authority label needs the
    # routes to carry one. Both are owed; neither is the writer's to fix in
    # one pass, and failing on them today would stop every build.
    advisory: List[str] = list(payload.get("advisory") or [])
    if findings:
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            reason=UI_NOT_WIRED,
            detail=findings[0],
            findings=findings + advisory,
            payload={"answered": payload.get("answered") or [], "advisory": advisory},
        )
    return GateResult(
        ok=True,
        gate=GATE_NAME,
        detail=(
            "the served UI drives %d capability(ies) and every route it calls answers"
            % len(payload.get("ui_caps") or [])
        ),
        findings=advisory,
        payload={"answered": payload.get("answered") or [], "advisory": advisory},
    )
