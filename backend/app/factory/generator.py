"""Generate a standalone product repository from a ProductPlan + blueprint."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

from app.cerebrum_product_kernel.provenance import build_provenance, hash_tree, write_provenance
from app.factory.blueprint import ProductBlueprint, blueprint_to_dict
from app.factory.build.supply_chain import (
    PYTHON_312_SLIM_FROM,
    assert_generated_dockerfile,
    render_cyclonedx_sbom,
)
from app.factory.hat_adapter import build_hat_manifests, build_workflows
from app.factory.paths import (
    UnsafeOutputDir,
    factory_outputs_root,
    is_safe_to_clean,
)
from app.factory.planner import CapabilityPlanner, ProductPlan, assert_generatable
from app.factory.resident_engineer import write_resident_engineer, write_store_docs
from app.product_dna.emit import emit_product_dna
from app.resident_engineer.ship.inject import inject_resident_runtime


class ProductGenerator:
    def __init__(
        self,
        blueprint: ProductBlueprint,
        plan: Optional[ProductPlan] = None,
        *,
        blocks_root: Optional[Path] = None,
        factory_shelf: Optional[Path] = None,
        factory_commit: str = "unknown",
        blocks_commit: str = "unknown",
        product_dna_version: str = "1.0.0",
        pin_versions: Optional[Dict[str, str]] = None,
    ):
        self._coder_report: Dict[str, Any] = {"written": [], "stubbed": {}}
        self.blueprint = blueprint
        self.planner = CapabilityPlanner(blocks_root, factory_shelf)
        self.plan = assert_generatable(plan) if plan else self.planner.plan(blueprint)
        self.factory_commit = factory_commit
        self.blocks_commit = blocks_commit
        self.blocks_root = blocks_root
        self.product_dna_version = product_dna_version
        self.pin_versions = dict(pin_versions or {})

    def generate(self, output_dir: Path | str, *, clean: bool = True) -> Dict[str, Any]:
        out = Path(output_dir).resolve()
        if clean and out.exists():
            # This is the line that actually destroys, so it refuses on its own
            # account rather than trusting whoever called it. Untrusted paths
            # are already confined to the outputs root at the HTTP boundary
            # (paths.safe_output_dir); this second check exists so a future
            # call site cannot reintroduce an arbitrary delete.
            if not is_safe_to_clean(out):
                raise UnsafeOutputDir(
                    f"refusing to clean {out}: outside {factory_outputs_root()} "
                    "and not a temporary directory"
                )
            shutil.rmtree(out)
        out.mkdir(parents=True, exist_ok=True)

        # Resident Engineer first — never bolted on after platform modules
        re_meta = write_resident_engineer(
            out,
            self.blueprint,
            self.plan,
            blocks_commit=self.blocks_commit,
            factory_commit=self.factory_commit,
        )
        write_store_docs(out)

        self._write_readme(out)
        self._write_pyproject(out)
        self._write_app(out)
        actions = self._write_actions(out)
        agents = self._write_hats(out)
        workflows = self._write_workflows(out)
        self._write_ui_stub(out)
        self._write_universal_console(out)
        self._write_connectors(out)
        self._copy_referenced_blocks(out)
        self._copy_referenced_kits(out)
        self._write_blueprint_copy(out)
        self._write_edge_profile(out)
        self._write_certification_scaffold(out)
        self._write_gates(out)
        self._write_env_example(out)
        if self.blueprint.vertical == "estate":
            self._write_estate_kit_surfaces(out)
        self._write_runtime_packaging(out)

        # Product DNA after catalogs exist — sole Resident Mode understanding surface.
        change_events = []
        build_event = (
            out / "product-agent" / "build_events" / "0001_resident_engineer_created.json"
        )
        if build_event.is_file():
            try:
                change_events.append(json.loads(build_event.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                change_events = []
        dna_meta = emit_product_dna(
            out,
            self.blueprint,
            self.plan,
            factory_commit=self.factory_commit,
            blocks_commit=self.blocks_commit,
            actions=actions,
            agents=agents,
            workflows=workflows,
            change_events=change_events,
            product_dna_version=self.product_dna_version,
            pin_versions=self.pin_versions,
        )
        # Resident Mode runtime package (flag-gated at runtime; always shipped)
        re_runtime = inject_resident_runtime(out)

        plan_dict = self.plan.to_dict()
        inputs_hash = hash_tree(out)
        prov = build_provenance(
            product_id=self.blueprint.product_id,
            blueprint_id=f"{self.blueprint.product_id}:{self.blueprint.schema_version}",
            factory_commit=self.factory_commit,
            blocks_commit=self.blocks_commit,
            plan=plan_dict,
            inputs_hash=inputs_hash,
        )
        write_provenance(out / "docs" / "provenance" / "provenance.json", prov)
        write_provenance(out / "factory_plan.json", plan_dict)

        return {
            "output_dir": str(out),
            "inputs_hash": inputs_hash,
            "product_id": self.blueprint.product_id,
            "plan": plan_dict,
            "provenance": prov,
            "resident_engineer": re_meta,
            "product_dna": dna_meta,
            "resident_runtime": re_runtime,
            # What the coder actually did for GENERATE capabilities — written
            # module ids vs {capability_id: reason} it fell back to stubs.
            # Degraded output is fine; invisible degradation is not.
            "coder": self._coder_report,
        }

    # --- Clone-and-test standard -------------------------------------------
    # Every generated pilot must let an IT/AI client clone, run, and VERIFY
    # without trusting the factory: one-command local run, gates inside the
    # repo, provenance, and honesty labels derived from the plan itself.

    def _write_readme(self, out: Path) -> None:
        bp = self.blueprint
        caps = self.plan.capabilities

        feature_rows = []
        for cap in caps:
            blocks = ", ".join("`" + b + "`" for b in cap.block_ids) or "— (kernel/GENERATE)"
            feature_rows.append(
                "| `" + cap.capability_id + "` | " + cap.strategy + " | " + blocks + " |"
            )
        feature_table = "\n".join(feature_rows) or "| — | — | — |"

        honesty = []
        for cap in caps:
            if cap.strategy == "GENERATE":
                honesty.append(
                    "- Capability `" + cap.capability_id + "` is generated scaffolding "
                    "(strategy GENERATE) — extend it via Factory templates, not by hand."
                )
            elif cap.strategy == "UNSUPPORTED":
                honesty.append(
                    "- Capability `" + cap.capability_id + "` is declared UNSUPPORTED and "
                    "fails closed by design."
                )
        for c in bp.connectors:
            honesty.append(
                "- Connector `" + c + "` is an honest stub (`not_implemented`) — no live "
                "third-party integration exists in this repo."
            )
        if bp.vertical == "estate":
            honesty.append(
                "- `data/demo/` fixtures are demonstration data, labeled as such — "
                "not real estate records."
            )
        honesty.append(
            "- This is a PILOT: production use requires separate certification. "
            "See `docs/certification/dual_certification.json`."
        )
        honesty_block = "\n".join(honesty)

        text = (
            "# " + bp.product_name + "\n\n"
            + bp.summary + "\n\n"
            + "Generated by the **CerebrumDev.ai** Factory from `" + bp.schema_version + "`.\n"
            + "Durable capability fixes belong in the Factory / Blocks / kernel, followed by\n"
            + "regeneration — do not hand-edit durable code in this repo.\n\n"
            + "- **Product id:** `" + bp.product_id + "`\n"
            + "- **Vertical:** " + bp.vertical + "\n"
            + "- **Factory commit:** `" + self.factory_commit + "`\n"
            + "- **Blocks commit:** `" + self.blocks_commit + "`\n\n"
            + "## Quickstart (clone-and-test)\n\n"
            + "```bash\n"
            + "python3 -m pip install -r requirements.txt\n"
            + "PYTHONPATH=. uvicorn app.main:app --port 8000\n"
            + "```\n\n"
            + "Open `http://127.0.0.1:8000/` — the **universal console**: every\n"
            + "capability as a card with a live Run panel (real action invocations,\n"
            + "honest kernel outcomes), plus workflows and agents. It is one\n"
            + "self-contained static page served by the app; regenerating with the\n"
            + "factory coder enabled replaces it with a capability-specific UI.\n"
            + "API check: `http://127.0.0.1:8000/health`. Then verify everything yourself:\n\n"
            + "```bash\n"
            + "python3 scripts/release_gate.py\n"
            + "```\n\n"
            + "The release gate runs the smoke suite in `tests/` against the live app\n"
            + "object and prints a PASS/FAIL verdict. A pilot is not done when it is\n"
            + "generated — it is done when a stranger can clone it and watch it pass.\n\n"
            + "## Gates\n\n"
            + "| Gate | What it proves |\n"
            + "| --- | --- |\n"
            + "| `tests/test_smoke.py` | `/health`, `/v1/capabilities`, action catalog, provenance file, block snapshot |\n"
            + "| `scripts/release_gate.py` | Runs the suite, prints verdict, exits non-zero on failure |\n\n"
            + "## Architecture\n\n"
            + "```text\n"
            + "app/\n"
            + "  main.py                  FastAPI entrypoint (health, capabilities, agents, workflows)\n"
            + "  actions/                 Capability actions (kernel ActionOutcome pattern)\n"
            + "  agents/manifests/        Agent hat manifests\n"
            + "  workflows/               Workflow definitions\n"
            + "  cerebrum_product_kernel/ Vendored kernel contract\n"
            + "  connectors/              Honest stub connectors (not_implemented)\n"
            + "vendor/blocks/             Versioned Store block snapshot (offline-verifiable)\n"
            + "kits/                      Kit packs for the product's capabilities\n"
            + "product-dna/               Entity model, rule overlay, test catalog, block lockfile\n"
            + "docs/\n"
            + "  blueprint/               The blueprint this repo was generated from\n"
            + "  provenance/              factory/blocks commits + inputs hash\n"
            + "  certification/           Dual certification scaffold (pending until certified)\n"
            + "frontend/                  Generated UI shell (modules per ui_modules)\n"
            + "scripts/release_gate.py    The clone-and-test verdict script\n"
            + "tests/                     Smoke suite executed by the gate\n"
            + "```\n\n"
            + "## Feature-to-block map\n\n"
            + "| Capability | Strategy | Store blocks |\n"
            + "| --- | --- | --- |\n"
            + feature_table + "\n\n"
            + "## Honesty notes\n\n"
            + honesty_block + "\n\n"
            + "## Provenance\n\n"
            + "See `docs/provenance/provenance.json` and `factory_plan.json`.\n"
        )
        (out / "README.md").write_text(text, encoding="utf-8")

    def _write_pyproject(self, out: Path) -> None:
        reqs = [
            "fastapi>=0.115.0",
            "pydantic>=2.0",
            "uvicorn>=0.30",
            "pyyaml>=6.0",
            "httpx>=0.27",
            "pytest>=8.0",
        ]
        rag_reqs = (
            Path(__file__).resolve().parent
            / "kits"
            / "private_estate_operations"
            / "steward_runtime"
            / "deploy"
            / "requirements-rag.txt"
        )
        if self.blueprint.vertical == "estate" and rag_reqs.is_file():
            for line in rag_reqs.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    reqs.append(stripped)
        (out / "requirements.txt").write_text("\n".join(reqs) + "\n", encoding="utf-8")

    def _write_env_example(self, out: Path) -> None:
        lines = [
            "# Copy to .env and fill in. Never commit real values.",
            "ENV=development",
            "PYTHONPATH=.",
            "RESIDENT_ENGINEER_ENABLED=false",
            "",
            "# NETWORK_POSTURE: RoleRunner products are P1 (offline strict, no store URL).",
            "# This ProductGenerator emitter still POSTs REUSE actions to the store",
            "# (S6 declared leftover). Optional here; unset → DEPENDENCY_REQUIRED, not a fake success.",
            "# CEREBRUM_API_URL=https://cerebrum-blocks.onrender.com",
            "# CEREBRUM_API_KEY=",
        ]
        if self.blueprint.vertical == "estate":
            lines += [
                "",
                "# Estate vertical — dual RAG (pilot path)",
                "# STEWARD_DATABASE_URL=postgresql://user:pass@host:5432/db",
                "# STEWARD_EMBED_BACKEND=fastembed",
                "# STEWARD_REQUIRE_PRODUCTION_EMBEDDINGS=1",
                "# STEWARD_REQUIRE_PERSISTENT_RAG=1",
                "# STEWARD_ALLOW_DEMO_AUTH_BYPASS=false",
            ]
        (out / ".env.example").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_gates(self, out: Path) -> None:
        """Ship the verification suite INSIDE the generated repo."""
        bp = self.blueprint
        tests_dir = out / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        (tests_dir / "__init__.py").write_text("", encoding="utf-8")

        cap_ids = [c.capability_id for c in self.plan.capabilities]
        smoke = '''"""Smoke suite for __PRODUCT_NAME__ — run by scripts/release_gate.py.

Proves the generated pilot is alive and self-describing without any
external service. Extend per product; never delete the provenance check.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ID = "__PRODUCT_ID__"
EXPECTED_CAPABILITIES = __CAP_IDS__

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["product_id"] == PRODUCT_ID


def test_capabilities_endpoint():
    r = client.get("/v1/capabilities")
    assert r.status_code == 200


def test_action_catalog_covers_plan():
    from app.actions import ACTION_CATALOG

    catalog_ids = {a["capability_id"] for a in ACTION_CATALOG}
    for cap_id in EXPECTED_CAPABILITIES:
        assert cap_id in catalog_ids, "capability missing from action catalog: " + cap_id


def test_provenance_present_and_hashed():
    prov_path = ROOT / "docs" / "provenance" / "provenance.json"
    assert prov_path.is_file(), "provenance.json missing — repo is not self-describing"
    prov = json.loads(prov_path.read_text(encoding="utf-8"))
    assert prov.get("inputs_hash"), "provenance has no inputs_hash"
    assert prov.get("product_id") == PRODUCT_ID


def test_block_snapshot_present():
    vendor = ROOT / "vendor" / "blocks"
    assert vendor.is_dir(), "vendor/blocks snapshot missing — offline verification impossible"


def test_factory_plan_present():
    plan_path = ROOT / "factory_plan.json"
    assert plan_path.is_file()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan, "factory_plan.json is empty"


def test_actions_are_reachable_over_http():
    """Every planned capability must be callable — a 404 here means the
    export self-describes but cannot do work (the 'skeleton' failure mode,
    field-found 2026-08-02). Without a configured block store the kernel
    answers 424 dependency_required; with one it answers 200. Both prove
    the route exists and dispatches to the real handler."""
    r = client.get("/v1/actions")
    assert r.status_code == 200
    for cap_id in EXPECTED_CAPABILITIES:
        resp = client.post(
            "/v1/actions/" + cap_id,
            json={"arguments": {"probe": True}},
            headers={"X-Tenant-Id": "smoke"},
        )
        assert resp.status_code != 404, "capability has no route: " + cap_id
        outcome = resp.json()
        assert outcome.get("status") in {
            "success",
            "dependency_required",
            "execution_error",
        }, "non-kernel outcome for " + cap_id + ": " + str(outcome)[:200]


def test_unknown_action_is_a_404_not_a_shrug():
    r = client.post("/v1/actions/definitely-not-a-capability", json={})
    assert r.status_code == 404


def test_console_ui_is_served_at_root():
    """The export must ship a usable UI, not only an API. The universal
    console is a self-contained page served at / — its absence is the
    'API-only skeleton' failure mode (field-found 2026-08-02)."""
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    body = r.text
    assert "/v1/capabilities" in body, "console does not read the plan"
    assert "/v1/actions/" in body, "console cannot invoke actions"
'''
        smoke = smoke.replace("__PRODUCT_NAME__", bp.product_name)
        smoke = smoke.replace("__PRODUCT_ID__", bp.product_id)
        smoke = smoke.replace("__CAP_IDS__", repr(cap_ids))
        if self.plan.dual_registered_blocks:
            smoke += '''

def test_kit_packs_present():
    kits = ROOT / "kits"
    assert kits.is_dir(), "kits/ missing — export is a runner, not a product tree"
    manifests = list(kits.glob("*/manifest.json"))
    assert manifests, "kits/ has no kit pack manifests"
'''
        (tests_dir / "test_smoke.py").write_text(smoke, encoding="utf-8")

        scripts_dir = out / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        gate = '''#!/usr/bin/env python3
"""Release gate for __PRODUCT_NAME__.

Runs the smoke suite and prints a PASS/FAIL verdict. This is the
clone-and-test contract: a client clones the repo, runs this script,
and watches the pilot prove itself.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    print("== __PRODUCT_NAME__ — release gate ==")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=ROOT,
    )
    ok = result.returncode == 0

    prov_path = ROOT / "docs" / "provenance" / "provenance.json"
    if prov_path.is_file():
        prov = json.loads(prov_path.read_text(encoding="utf-8"))
        print("provenance product_id:", prov.get("product_id"))
        print("provenance inputs_hash:", str(prov.get("inputs_hash"))[:16] + "...")
    else:
        print("provenance: MISSING")
        ok = False

    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
'''
        gate = gate.replace("__PRODUCT_NAME__", bp.product_name)
        (scripts_dir / "release_gate.py").write_text(gate, encoding="utf-8")

    # --- End clone-and-test standard ----------------------------------------

    def _write_app(self, out: Path) -> None:
        app = out / "app"
        app.mkdir(parents=True, exist_ok=True)
        (app / "__init__.py").write_text(
            f'"""Generated product package for {self.blueprint.product_id}."""\n'
            f'PRODUCT_ID = "{self.blueprint.product_id}"\n'
            f'VERTICAL = "{self.blueprint.vertical}"\n',
            encoding="utf-8",
        )
        # Vendor neutralized kernel contract into generated product
        kernel_src = Path(__file__).resolve().parents[1] / "cerebrum_product_kernel"
        kernel_dst = app / "cerebrum_product_kernel"
        if kernel_dst.exists():
            shutil.rmtree(kernel_dst)
        shutil.copytree(
            kernel_src,
            kernel_dst,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        # Rewrite imports inside generated copy to app.cerebrum_product_kernel
        for py in kernel_dst.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            py.write_text(
                text.replace(
                    "from app.cerebrum_product_kernel",
                    "from app.cerebrum_product_kernel",
                ),
                encoding="utf-8",
            )

        if self.blueprint.vertical == "estate":
            main_py = self._estate_main_py()
        else:
            main_py = self._basic_main_py()
        (app / "main.py").write_text(main_py, encoding="utf-8")

    # Appended to every generated main.py. Without this the action modules
    # exist but no HTTP route reaches them — the export self-describes and
    # does nothing, which field-tested as "a skeleton" (2026-08-02).
    _ACTIONS_ROUTE_PY = '''

@app.post("/v1/actions/{capability_id}")
async def run_action(capability_id: str, request: Request):
    """Invoke a generated capability action (kernel ActionOutcome contract).

    Body: {"arguments": {...}} (or the arguments object directly).
    Tenant comes from X-Tenant-Id (defaults to "default" — these exports are
    single-tenant per deployment).
    """
    import importlib

    from app.actions import ACTION_CATALOG

    spec = next(
        (s for s in ACTION_CATALOG if s["capability_id"] == capability_id), None
    )
    if spec is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "unknown_capability",
                "capability_id": capability_id,
                "known": [s["capability_id"] for s in ACTION_CATALOG],
            },
        )

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    arguments = body.get("arguments") if isinstance(body.get("arguments"), dict) else body

    module_name = capability_id.replace("-", "_")
    mod = importlib.import_module("app.actions." + module_name)
    context = {
        "tenant_id": request.headers.get("X-Tenant-Id") or "default",
        "capability_id": capability_id,
        "action_id": spec["action_id"],
    }
    outcome = await mod.handle(context, arguments or {})

    # ActionOutcome.status -> HTTP, so callers and monitors see failure as
    # failure instead of a 200 that must be parsed to be believed.
    status_http = {
        "success": 200,
        "dependency_required": 424,
        "validation_error": 422,
        "permission_denied": 403,
        "unsupported": 501,
        "execution_error": 502,
    }.get(str(outcome.get("status")), 500)
    return JSONResponse(status_code=status_http, content=outcome)


@app.get("/v1/actions")
def list_actions():
    from app.actions import ACTION_CATALOG

    return {"actions": ACTION_CATALOG}


@app.get("/", include_in_schema=False)
def console():
    """Standard universal console — self-contained static page, no build step.

    Data-driven from this platform's own APIs so the same page works on
    every export; regenerate with the coder enabled to specialise it.
    """
    from pathlib import Path as _Path

    from fastapi.responses import HTMLResponse

    page = _Path(__file__).resolve().parent / "static" / "console.html"
    if not page.is_file():
        raise HTTPException(status_code=404, detail="console asset missing")
    return HTMLResponse(page.read_text(encoding="utf-8"))
'''

    def _basic_main_py(self) -> str:
        bp = self.blueprint
        return f'''"""Generated FastAPI entrypoint for {bp.product_id}."""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="{bp.product_name}", version="1.0.0")


@app.get("/health")
def health():
    return {{
        "ok": True,
        "product_id": "{bp.product_id}",
        "vertical": "{bp.vertical}",
        "human_authority": {str(bp.human_authority)},
    }}


@app.get("/v1/capabilities")
def capabilities():
    import json
    from pathlib import Path
    plan = json.loads((Path(__file__).resolve().parents[1] / "factory_plan.json").read_text())
    return plan


@app.get("/v1/agents")
def agents():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parent / "agents" / "manifests"
    return [json.loads(p.read_text()) for p in sorted(root.glob("*.json"))]


@app.get("/v1/workflows")
def workflows():
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parent / "workflows" / "workflows.json"
    return json.loads(path.read_text())
''' + self._ACTIONS_ROUTE_PY

    def _estate_main_py(self) -> str:
        bp = self.blueprint
        return f'''"""Generated FastAPI entrypoint for {bp.product_id}."""

from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app.steward.errors import attach_request_id_middleware
from app.steward.probes import health_payload, readiness_checks, version_payload

app = FastAPI(title="{bp.product_name}", version="1.0.0")

app.middleware("http")(attach_request_id_middleware)

PRODUCT_ID = "{bp.product_id}"
VERTICAL = "{bp.vertical}"
HUMAN_AUTHORITY = {str(bp.human_authority)}


@app.get("/health")
def health():
    return health_payload(
        product_id=PRODUCT_ID,
        vertical=VERTICAL,
        human_authority=HUMAN_AUTHORITY,
    )


@app.get("/ready")
def ready():
    payload = readiness_checks()
    status = 200 if payload.get("ready") else 503
    return Response(
        content=__import__("json").dumps(payload),
        media_type="application/json",
        status_code=status,
    )


@app.get("/version")
def version():
    return version_payload(product_id=PRODUCT_ID)


@app.get("/v1/capabilities")
def capabilities():
    import json
    from pathlib import Path
    plan = json.loads((Path(__file__).resolve().parents[1] / "factory_plan.json").read_text())
    return plan


@app.get("/v1/agents")
def agents():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parent / "agents" / "manifests"
    return [json.loads(p.read_text()) for p in sorted(root.glob("*.json"))]


@app.get("/v1/workflows")
def workflows():
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parent / "workflows" / "workflows.json"
    return json.loads(path.read_text())


# Estate kit demo fixtures (legacy /v1/rag/* gated inside router when disabled).
try:
    from app.estate_kit import router as estate_kit_router

    app.include_router(estate_kit_router)
except ImportError as exc:
    raise RuntimeError(
        "Estate kit surfaces are mandatory for estate vertical products"
    ) from exc

# Production Steward RAG (Postgres/pgvector + packs) — canonical pilot path.
try:
    from app.steward.api import router as steward_rag_router

    app.include_router(steward_rag_router)
except ImportError as exc:
    raise RuntimeError(
        "Steward production RAG runtime is mandatory for estate vertical products"
    ) from exc

# Optional pilot fixture seed for local/CI when explicitly enabled.
if os.getenv("STEWARD_PILOT_SEED_FIXTURE", "0").lower() in {{"1", "true", "yes", "on"}}:
    from app.steward.auth import seed_pilot_fixture
    from app.steward.db import init_engine, session_scope

    init_engine()
    with session_scope() as session:
        seed_pilot_fixture(session)
''' + self._ACTIONS_ROUTE_PY

    def _write_estate_kit_surfaces(self, out: Path) -> None:
        """Emit demo fixtures + dual RAG API for estate vertical products."""
        kit_fixtures = (
            Path(__file__).resolve().parent
            / "kits"
            / "private_estate_operations"
            / "fixtures"
            / "demo_estate.json"
        )
        data_dir = out / "data" / "demo"
        data_dir.mkdir(parents=True, exist_ok=True)
        if kit_fixtures.is_file():
            shutil.copy2(kit_fixtures, data_dir / "estate_fixtures.json")
        else:
            (data_dir / "estate_fixtures.json").write_text(
                json.dumps({"properties": [], "honesty": "fixtures missing at generate"}, indent=2)
                + "\n",
                encoding="utf-8",
            )

        dual_rag = {
            "schema_version": "1.1.0",
            "layers": {
                "1": {
                    "id": "sop_standards",
                    "name": "SOP / House Manual / global standards",
                    "index": "steward_sop_v1",
                    "project": "prebuilt_steward_core",
                    "tenant": "platform",
                    "source_field": "sop_corpus",
                    "blocks": ["document_engine", "knowledge", "vector_search", "database"],
                },
                "2": {
                    "id": "estate_documents",
                    "name": "Estate documents (separately indexed)",
                    "index": "steward_estate_docs_v1",
                    "source_field": "estate_documents",
                    "blocks": ["document_engine", "knowledge", "vector_search", "database"],
                },
            },
            "demo_path": {
                "embedding_provider": "local_feature_hash_v1",
                "vector_adapter": "local_flat_json_v1",
                "routes": ["/v1/rag/query", "/v1/rag/ingest", "/v1/rag/dual"],
                "notes": "Zero-deps CI/local fallback when STEWARD_DATABASE_URL is unset.",
            },
            "pilot_path": {
                "embedding_provider": "fastembed:BAAI/bge-small-en-v1.5",
                "vector_adapter": "postgres_jsonb_v1",
                "routes": ["/v1/rag/query", "/v1/rag/ingest", "/v1/rag/dual", "/v1/rag/bootstrap"],
                "env": [
                    "STEWARD_DATABASE_URL",
                    "STEWARD_EMBED_BACKEND=fastembed",
                    "STEWARD_REQUIRE_PRODUCTION_EMBEDDINGS=1",
                    "STEWARD_REQUIRE_PERSISTENT_RAG=1",
                ],
            },
            "production_path": {
                "embedding_provider": "fastembed:BAAI/bge-small-en-v1.5",
                "vector_adapter": "postgres_pgvector_v1",
                "routes": [
                    "/v1/steward/rag/query",
                    "/v1/steward/rag/ingest",
                    "/v1/steward/packs",
                    "/v1/steward/facilities",
                    "/v1/steward/fleet",
                ],
                "packs": [
                    "steward_service_core_open_v1",
                    "steward_hospitality_intents_v1",
                    "steward_facilities_open_v1",
                    "steward_fleet_open_v1",
                ],
            },
            "embedding_provider": "env:STEWARD_EMBED_BACKEND",
            "vector_adapter": "env:STEWARD_RAG_PERSISTENCE",
            "honesty": (
                "/v1/rag/* serves the live dual RAG path. With STEWARD_DATABASE_URL set, "
                "chunks/embeddings persist in Postgres (postgres_jsonb_v1). With "
                "STEWARD_EMBED_BACKEND=fastembed, live embedding_provider is "
                "fastembed:BAAI/bge-small-en-v1.5. STEWARD_REQUIRE_PRODUCTION_EMBEDDINGS=1 "
                "and STEWARD_REQUIRE_PERSISTENT_RAG=1 fail closed (no silent hash/JSONL). "
                "Full hybrid RRF + governed packs remain on /v1/steward/rag/*."
            ),
        }
        rag_docs = out / "docs" / "rag"
        rag_docs.mkdir(parents=True, exist_ok=True)
        (rag_docs / "dual_rag.json").write_text(
            json.dumps(dual_rag, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        kit_root = Path(__file__).resolve().parent / "kits" / "private_estate_operations"
        estate_mod = out / "app" / "estate_kit"
        estate_mod.mkdir(parents=True, exist_ok=True)
        (estate_mod / "__init__.py").write_text(
            '"""Factory-generated estate kit surfaces (demo + dual RAG)."""\n'
            "from app.estate_kit.router import router\n\n"
            '__all__ = ["router"]\n',
            encoding="utf-8",
        )
        router_src = kit_root / "estate_kit_router.py"
        if router_src.is_file():
            shutil.copy2(router_src, estate_mod / "router.py")
        rag_src = kit_root / "rag"
        rag_dst = estate_mod / "rag"
        if rag_dst.exists():
            shutil.rmtree(rag_dst)
        if rag_src.is_dir():
            shutil.copytree(
                rag_src,
                rag_dst,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )

        # Production Steward RAG runtime (Postgres/pgvector + governed packs)
        steward_src = kit_root / "steward_runtime"
        steward_dst = out / "app" / "steward"
        if steward_dst.exists():
            shutil.rmtree(steward_dst)
        if steward_src.is_dir():
            shutil.copytree(
                steward_src,
                steward_dst,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            compose_src = steward_src / "deploy" / "docker-compose.yml"
            if compose_src.is_file():
                deploy_dir = out / "deploy"
                deploy_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(compose_src, deploy_dir / "docker-compose.steward-rag.yml")

        # SPA deep-link stubs for estate modules (cold-start friendly)
        spa = out / "frontend" / "src" / "routes"
        spa.mkdir(parents=True, exist_ok=True)
        (spa / "deepLinks.ts").write_text(
            "// Factory-generated deep links for Steward SPA\n"
            "export const DEEP_LINKS = {\n"
            "  home: '/',\n"
            "  registry: '/registry',\n"
            "  houseManual: '/house-manual',\n"
            "  maintenance: '/maintenance',\n"
            "  vendors: '/vendors',\n"
            "  staff: '/staff',\n"
            "  principal: '/principal',\n"
            "  onboarding: '/onboarding',\n"
            "  rag: '/rag',\n"
            "  resident: '/resident',\n"
            "} as const;\n"
            "\n"
            "/** Cold-start retry hint for Render free-tier spin-up. */\n"
            "export const COLD_START = {\n"
            "  retries: 3,\n"
            "  backoffMs: [1000, 3000, 8000],\n"
            "  message: 'Service waking up — retrying…',\n"
            "};\n",
            encoding="utf-8",
        )

    def _write_runtime_packaging(self, out: Path) -> None:
        """Emit Dockerfile + Procfile so generated products are Render-deployable."""
        docker_lines = [
            f"FROM {PYTHON_312_SLIM_FROM}",
            "WORKDIR /app",
            "ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1",
            "COPY requirements.txt .",
            "RUN pip install --no-cache-dir -r requirements.txt",
        ]
        if self.blueprint.vertical == "estate":
            # Warm FastEmbed ONNX weights at build time so first request is not a download.
            docker_lines.append(
                "RUN python -c \"from fastembed import TextEmbedding; "
                "TextEmbedding(model_name='BAAI/bge-small-en-v1.5')\" "
                "|| echo 'fastembed warm skipped'"
            )
        docker_lines.extend(
            [
                "COPY . .",
                "ENV PYTHONPATH=/app",
                "# F19: a red suite must not produce a deployable image.",
                "RUN python3 scripts/release_gate.py",
                "EXPOSE 8000",
                'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]',
            ]
        )
        docker_text = "\n".join(docker_lines) + "\n"
        assert_generated_dockerfile(docker_text)
        (out / "Dockerfile").write_text(docker_text, encoding="utf-8")
        req_text = ""
        req_path = out / "requirements.txt"
        if req_path.is_file():
            req_text = req_path.read_text(encoding="utf-8")
        block_ids = []
        for cap in self.plan.capabilities:
            block_ids.extend(cap.block_ids or [])
        (out / "docs").mkdir(parents=True, exist_ok=True)
        (out / "docs" / "sbom.cdx.json").write_text(
            render_cyclonedx_sbom(
                product_id=self.blueprint.product_id,
                product_name=self.blueprint.product_name,
                image_ref=PYTHON_312_SLIM_FROM,
                requirements_text=req_text,
                blocks=block_ids,
            ),
            encoding="utf-8",
        )
        (out / "Procfile").write_text(
            "web: uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}\n",
            encoding="utf-8",
        )
        (out / "render.yaml").write_text(
            f"""services:
  - type: web
    name: {self.blueprint.product_id}
    runtime: docker
    plan: starter
    healthCheckPath: /health
    envVars:
      - key: RESIDENT_ENGINEER_ENABLED
        value: "false"
      - key: PYTHONPATH
        value: /app
      - key: STEWARD_EMBED_BACKEND
        value: fastembed
      - key: STEWARD_REQUIRE_PRODUCTION_EMBEDDINGS
        value: "1"
      - key: STEWARD_REQUIRE_PERSISTENT_RAG
        value: "1"
      - key: STEWARD_LEGACY_RAG_ENABLED
        value: "false"
      - key: STEWARD_ALLOW_DEMO_AUTH_BYPASS
        value: "false"
      - key: STEWARD_ADMIN_ROUTES_ENABLED
        value: "false"
      - key: STEWARD_RAG_PERSISTENCE
        value: postgres
      - key: STEWARD_DATABASE_URL
        fromDatabase:
          name: {self.blueprint.product_id}-db
          property: connectionString
databases:
  - name: {self.blueprint.product_id}-db
    plan: basic-256mb
    postgresMajorVersion: "16"
""",
            encoding="utf-8",
        )

    def _write_actions(self, out: Path) -> list:
        from .coder import CoderError, coder_budget_s, coder_enabled, generate_handler_body

        actions_dir = out / "app" / "actions"
        actions_dir.mkdir(parents=True, exist_ok=True)
        specs = []
        self._coder_report = {"written": [], "stubbed": {}}
        bp_caps = {c.id: c for c in self.blueprint.capabilities}
        # Wall-clock budget across ALL GENERATE capabilities of this
        # generation — bounds how long one HTTP request can hold the worker.
        budget = coder_budget_s()
        deadline = (time.monotonic() + budget) if budget > 0 else None
        for cap in self.plan.capabilities:
            action_name = cap.capability_id.replace("-", "_")
            domain = self.blueprint.vertical.replace("-", "_")
            # action_id must be domain.action
            action_id = f"{domain}.{action_name}"
            specs.append(
                {
                    "action_id": action_id,
                    "capability_id": cap.capability_id,
                    "strategy": cap.strategy,
                    "block_ids": cap.block_ids,
                    "notes": cap.notes,
                }
            )
            mod = actions_dir / f"{action_name}.py"

            # GENERATE strategy: the coder writes the handler. On any coder
            # failure the honest stub ships instead and the reason is
            # recorded — never a fabricated success, never a silent stub.
            if cap.strategy == "GENERATE":
                bp_cap = bp_caps.get(cap.capability_id)
                if not coder_enabled():
                    self._coder_report["stubbed"][cap.capability_id] = (
                        "coder disabled (FACTORY_CODER_ENABLED=0)"
                    )
                elif bp_cap is None:
                    self._coder_report["stubbed"][cap.capability_id] = (
                        "capability missing from blueprint"
                    )
                elif deadline is not None and time.monotonic() >= deadline:
                    self._coder_report["stubbed"][cap.capability_id] = (
                        f"coder budget exhausted ({budget:g}s total for this "
                        "generation, FACTORY_CODER_BUDGET_S); honest stub shipped"
                    )
                else:
                    try:
                        impl = generate_handler_body(bp_cap, self.blueprint)
                        mod.write_text(
                            self._coder_module_py(action_id, cap, impl),
                            encoding="utf-8",
                        )
                        self._coder_report["written"].append(cap.capability_id)
                        continue
                    except CoderError as exc:
                        self._coder_report["stubbed"][cap.capability_id] = str(exc)

            mod.write_text(
                f'''"""Generated action module for {action_id} (strategy={cap.strategy}).

Uses cerebrum_product_kernel ActionOutcome patterns. Durable logic belongs in
Factory templates / dual-registered blocks — regenerate rather than hand-edit.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

import httpx

from app.cerebrum_product_kernel.contract.models import (
    ActionEvidence,
    ActionOutcome,
    ActionStatus,
)

ACTION_ID = "{action_id}"
CAPABILITY_ID = "{cap.capability_id}"
STRATEGY = "{cap.strategy}"
BLOCK_IDS: List[str] = {cap.block_ids!r}


async def handle(context: Dict[str, Any], arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Execute capability with kernel-shaped outcome (not a freehand echo)."""
    tenant_id = context.get("tenant_id")
    if not tenant_id:
        outcome = ActionOutcome(
            status=ActionStatus.PERMISSION_DENIED,
            error_code="missing_tenant",
            error_message="tenant_id is required in trusted context",
        )
        return outcome.to_dict()

    if STRATEGY == "UNSUPPORTED":
        outcome = ActionOutcome(
            status=ActionStatus.UNSUPPORTED,
            error_code="unsupported_capability",
            error_message=f"capability {{CAPABILITY_ID}} is UNSUPPORTED",
        )
        return outcome.to_dict()

    # No store block bound (GENERATE strategy): do not claim work was done —
    # surface honestly rather than returning a templated success.
    if not BLOCK_IDS:
        outcome = ActionOutcome(
            status=ActionStatus.DEPENDENCY_REQUIRED,
            error_code="no_block_bound",
            error_message=(
                "capability " + CAPABILITY_ID + " is strategy " + STRATEGY
                + " with no dual-registered block to invoke"
            ),
        )
        return outcome.to_dict()

    # REUSE: actually invoke each block via the Cerebrum-Blocks store
    # (POST /v1/execute). Real output, real evidence — never a canned string.
    store_url = (os.getenv("CEREBRUM_API_URL") or "").rstrip("/")
    if not store_url:
        outcome = ActionOutcome(
            status=ActionStatus.DEPENDENCY_REQUIRED,
            error_code="store_unconfigured",
            error_message=(
                "block store not configured — set CEREBRUM_API_URL to invoke "
                + repr(BLOCK_IDS) + "; no block was executed"
            ),
        )
        return outcome.to_dict()

    headers = {{"Content-Type": "application/json"}}
    key = os.getenv("CEREBRUM_API_KEY") or os.getenv("CEREBRUM_API_TOKEN")
    if key:
        headers["Authorization"] = "Bearer " + key

    results: Dict[str, Any] = {{}}
    errors: Dict[str, str] = {{}}
    evidence = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        for bid in BLOCK_IDS:
            try:
                resp = await client.post(
                    store_url + "/v1/execute",
                    json={{"block": bid, "input": arguments or {{}}, "params": {{}}}},
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                results[bid] = data
                evidence.append(
                    ActionEvidence(
                        source_id=bid,
                        filename="block:" + bid,
                        excerpt=str(data.get("result", data))[:280],
                        metadata={{"strategy": STRATEGY, "invoked": True}},
                    )
                )
            except Exception as exc:  # noqa: BLE001
                errors[bid] = type(exc).__name__ + ": " + str(exc)

    if not results:
        outcome = ActionOutcome(
            status=ActionStatus.EXECUTION_ERROR,
            error_code="block_invocation_failed",
            error_message="all blocks failed: " + repr(errors),
        )
        return outcome.to_dict()

    output = {{
        "action_id": ACTION_ID,
        "capability_id": CAPABILITY_ID,
        "strategy": STRATEGY,
        "block_ids": BLOCK_IDS,
        "arguments": arguments or {{}},
        "tenant_id": tenant_id,
        "result": {{
            "ok": True,
            "blocks_used": list(results.keys()),
            "block_results": results,
            "block_errors": errors or None,
        }},
    }}
    outcome = ActionOutcome.success(output, evidence=evidence)
    return outcome.to_dict()
''',
                encoding="utf-8",
            )
        (actions_dir / "__init__.py").write_text(
            "ACTION_CATALOG = " + json.dumps(specs, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return specs

    def _coder_module_py(self, action_id: str, cap, impl: Dict[str, Any]) -> str:
        """Module for a coder-written GENERATE capability.

        The emitted body runs inside try/except and answers with kernel
        outcomes — a broken generated body is an execution_error response,
        never a crashed product. Provenance (model, capability) is stamped
        in the header for review.
        """
        return f'''"""Coder-written action module for {action_id} (strategy=GENERATE).

Written by the factory coder LLM ({impl["model"]}) from the approved
blueprint. Review before production use. Regenerate rather than hand-edit;
durable fixes belong in the Factory.
"""

from __future__ import annotations

from typing import Any, Dict

from app.cerebrum_product_kernel.contract.models import (
    ActionOutcome,
    ActionStatus,
)

ACTION_ID = "{action_id}"
CAPABILITY_ID = "{cap.capability_id}"
STRATEGY = "GENERATE"
BLOCK_IDS: list = []
CODER_MODEL = "{impl["model"]}"

# In-memory state for the generated logic. Swap for a durable store block
# via regeneration when this capability graduates from prototype.
_STATE: Dict[str, Any] = {{}}


async def generated_logic(context: Dict[str, Any], arguments: Dict[str, Any]) -> Dict[str, Any]:
{impl["body"]}


async def handle(context: Dict[str, Any], arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the coder-written capability with kernel-shaped outcomes."""
    tenant_id = context.get("tenant_id")
    if not tenant_id:
        return ActionOutcome(
            status=ActionStatus.PERMISSION_DENIED,
            error_code="missing_tenant",
            error_message="tenant_id is required in trusted context",
        ).to_dict()

    try:
        result = await generated_logic(context, arguments or {{}})
    except Exception as exc:  # noqa: BLE001 — generated code must not crash the product
        return ActionOutcome(
            status=ActionStatus.EXECUTION_ERROR,
            error_code="generated_logic_error",
            error_message=type(exc).__name__ + ": " + str(exc),
        ).to_dict()

    output = {{
        "action_id": ACTION_ID,
        "capability_id": CAPABILITY_ID,
        "strategy": STRATEGY,
        "coder_model": CODER_MODEL,
        "arguments": arguments or {{}},
        "tenant_id": tenant_id,
        "result": result,
    }}
    return ActionOutcome.success(output).to_dict()
'''

    def _write_hats(self, out: Path) -> list:
        manifests = build_hat_manifests(self.blueprint, self.plan)
        hats_dir = out / "app" / "agents" / "manifests"
        hats_dir.mkdir(parents=True, exist_ok=True)
        index = []
        for m in manifests:
            fname = m["agent_id"].replace(".", "_") + ".json"
            (hats_dir / fname).write_text(
                json.dumps(m, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            index.append({"agent_id": m["agent_id"], "kind": m["kind"], "file": fname})
        (out / "app" / "agents" / "__init__.py").write_text(
            "HAT_INDEX = " + json.dumps(index, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return manifests

    def _write_universal_console(self, out: Path) -> None:
        """Ship the standard universal console into the export.

        One self-contained static HTML page (inline CSS/JS, no build step,
        no external requests) served by the generated app at ``/``. It is
        data-driven from the export's own APIs — /health, /v1/capabilities,
        /v1/actions, /v1/workflows, /v1/agents — so the identical asset
        works on every platform the factory ships. Capability-specific UI
        is the coder's job at regeneration time; a missing UI is not.
        """
        src = Path(__file__).resolve().parent / "standards" / "universal_console.html"
        if not src.is_file():
            raise RuntimeError(
                "universal_console.html missing from factory standards — "
                "the export would ship without its UI"
            )
        static_dir = out / "app" / "static"
        static_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, static_dir / "console.html")

    def _write_workflows(self, out: Path) -> list:
        workflows = build_workflows(self.blueprint, self.plan)
        wf_dir = out / "app" / "workflows"
        wf_dir.mkdir(parents=True, exist_ok=True)
        (wf_dir / "workflows.json").write_text(
            json.dumps(workflows, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (wf_dir / "__init__.py").write_text(
            "WORKFLOWS = " + json.dumps(workflows, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return workflows

    def _write_ui_stub(self, out: Path) -> None:
        ui = out / "frontend" / "src"
        modules_dir = ui / "modules"
        modules_dir.mkdir(parents=True, exist_ok=True)
        modules = self.blueprint.ui_modules or ["command_center"]
        cap_ids = [c.capability_id for c in self.plan.capabilities]
        for mod in modules:
            safe = re.sub(r"[^a-zA-Z0-9_]", "_", mod)
            component = safe.title().replace("_", "") + "Module"
            (modules_dir / f"{safe}.tsx").write_text(
                f"""/* Generated UI module: {mod} — Factory template, regenerate-only. */
import {{ useEffect, useState }} from "react";

const MODULE_ID = "{mod}";
const PRODUCT_ID = "{self.blueprint.product_id}";
const CAPABILITIES = {json.dumps(cap_ids)};

export default function {component}() {{
  const [health, setHealth] = useState<string>("pending");
  useEffect(() => {{
    fetch("/health")
      .then((r) => (r.ok ? "ok" : "degraded"))
      .catch(() => "unreachable")
      .then(setHealth);
  }}, []);
  return (
    <section data-module={{MODULE_ID}} data-product={{PRODUCT_ID}}>
      <header>
        <h2>{{MODULE_ID}}</h2>
        <p>Runtime health: {{health}}</p>
      </header>
      <ul>
        {{CAPABILITIES.map((id) => (
          <li key={{id}} data-capability={{id}}>
            {{id}}
          </li>
        ))}}
      </ul>
    </section>
  );
}}
""",
                encoding="utf-8",
            )
        imports = "\n".join(
            f"import {re.sub(r'[^a-zA-Z0-9_]', '_', m).title().replace('_', '')}Module "
            f"from './modules/{re.sub(r'[^a-zA-Z0-9_]', '_', m)}';"
            for m in modules
        )
        renders = "\n".join(
            f"      <{re.sub(r'[^a-zA-Z0-9_]', '_', m).title().replace('_', '')}Module />"
            for m in modules
        )
        (ui / "App.tsx").write_text(
            f"/* Generated UI shell for {self.blueprint.product_name} */\n"
            f"{imports}\n"
            f"export default function App(){{\n"
            f"  return (\n"
            f"    <main data-product=\"{self.blueprint.product_id}\">\n"
            f"      <h1>{self.blueprint.product_name}</h1>\n"
            f"      <p>{self.blueprint.summary}</p>\n"
            f"{renders}\n"
            f"    </main>\n"
            f"  )\n"
            f"}}\n",
            encoding="utf-8",
        )
        (out / "frontend" / "package.json").write_text(
            json.dumps(
                {"name": self.blueprint.product_id, "private": True, "version": "1.0.0"},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def _write_connectors(self, out: Path) -> None:
        conn = out / "app" / "connectors"
        conn.mkdir(parents=True, exist_ok=True)
        items = []
        for c in self.blueprint.connectors:
            items.append({"id": c, "status": "not_implemented", "honest": True})
            (conn / f"{c}.py").write_text(
                f'"""Honest stub connector: {c}."""\n\nSTATUS = "not_implemented"\n',
                encoding="utf-8",
            )
        (conn / "__init__.py").write_text(
            "CONNECTORS = " + json.dumps(items, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _copy_referenced_blocks(self, out: Path) -> None:
        dest = out / "vendor" / "blocks"
        dest.mkdir(parents=True, exist_ok=True)
        mirror = Path(__file__).resolve().parent / "vendor_blocks_mirror"
        registry = Path(self.blocks_root) / "block_registry" if self.blocks_root else None
        for bid in self.plan.dual_registered_blocks:
            src = registry / bid if registry else None
            if src and src.exists():
                shutil.copytree(src, dest / bid, dirs_exist_ok=True)
                continue
            # Fall back to the vendor mirror when the external registry is unavailable
            mirror_src = mirror / bid
            if mirror_src.exists():
                shutil.copytree(mirror_src, dest / bid, dirs_exist_ok=True)

    def _copy_referenced_kits(self, out: Path) -> None:
        from app.factory.kit_pack import stock_kits

        if not self.plan.dual_registered_blocks:
            return
        stock_kits(
            out,
            self.plan.dual_registered_blocks,
            blocks_root=self.blocks_root,
        )

    def _write_blueprint_copy(self, out: Path) -> None:
        docs = out / "docs" / "blueprint"
        docs.mkdir(parents=True, exist_ok=True)
        (docs / "product_blueprint.json").write_text(
            json.dumps(blueprint_to_dict(self.blueprint), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _write_edge_profile(self, out: Path) -> None:
        profile = {
            "edge_profile": self.blueprint.edge_profile,
            "human_authority": self.blueprint.human_authority,
            "isolation": "property",
            "pii_in_learning": False,
        }
        (out / "docs" / "edge_profile.json").write_text(
            json.dumps(profile, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def _write_certification_scaffold(self, out: Path) -> None:
        cert = out / "docs" / "certification"
        cert.mkdir(parents=True, exist_ok=True)
        report = {
            "PRODUCT_CERTIFIED": "pending",
            "RESIDENT_AGENT_CERTIFIED": "pending",
            "incomplete_until_both": True,
            "factory_scenario": self.blueprint.factory_scenario.value,
        }
        (cert / "dual_certification.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


def git_head(repo: Path) -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except OSError:
        pass
    return "unknown"
