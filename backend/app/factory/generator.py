"""Generate a standalone product repository from a ProductPlan + blueprint."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from app.cerebrum_product_kernel.provenance import build_provenance, hash_tree, write_provenance
from app.factory.blueprint import ProductBlueprint, blueprint_to_dict
from app.factory.hat_adapter import build_hat_manifests, build_workflows
from app.factory.planner import CapabilityPlanner, ProductPlan
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
        self.blueprint = blueprint
        self.planner = CapabilityPlanner(blocks_root, factory_shelf)
        self.plan = plan or self.planner.plan(blueprint)
        self.factory_commit = factory_commit
        self.blocks_commit = blocks_commit
        self.blocks_root = blocks_root
        self.product_dna_version = product_dna_version
        self.pin_versions = dict(pin_versions or {})

    def generate(self, output_dir: Path | str, *, clean: bool = True) -> Dict[str, Any]:
        out = Path(output_dir).resolve()
        if clean and out.exists():
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
        self._write_connectors(out)
        self._copy_referenced_blocks(out)
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
            blocks = ", ".join(f"`{b}`" for b in cap.block_ids) or "— (kernel/GENERATE)"
            feature_rows.append(
                f"| `{cap.capability_id}` | {cap.strategy} | {blocks} |"
            )
        feature_table = "\n".join(feature_rows) or "| — | — | — |"

        honesty = []
        for cap in caps:
            if cap.strategy == "GENERATE":
                honesty.append(
                    f"- Capability `{cap.capability_id}` is generated scaffolding "
                    "(strategy GENERATE) — extend it via Factory templates, not by hand."
                )
            elif cap.strategy == "UNSUPPORTED":
                honesty.append(
                    f"- Capability `{cap.capability_id}` is declared UNSUPPORTED and "
                    "fails closed by design."
                )
        for c in bp.connectors:
            honesty.append(
                f"- Connector `{c}` is an honest stub (`not_implemented`) — no live "
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

        text = f"""# {bp.product_name}

{bp.summary}

Generated by the **CerebrumDev.ai** Factory from `{bp.schema_version}`.
Durable capability fixes belong in the Factory / Blocks / kernel, followed by
regeneration — do not hand-edit durable code in this repo.

- **Product id:** `{bp.product_id}`
- **Vertical:** {bp.vertical}
- **Factory commit:** `{self.factory_commit}`
- **Blocks commit:** `{self.blocks_commit}`

## Quickstart (clone-and-test)

```bash
python3 -m pip install -r requirements.txt
PYTHONPATH=. uvicorn app.main:app --port 8000
```

Open `http://127.0.0.1:8000/health`. Then verify everything yourself:

```bash
python3 scripts/release_gate.py
```

The release gate runs the smoke suite in `tests/` against the live app
object and prints a PASS/FAIL verdict. A pilot is not done when it is
generated — it is done when a stranger can clone it and watch it pass.

## Gates

| Gate | What it proves |
| --- | --- |
| `tests/test_smoke.py` | `/health`, `/v1/capabilities`, action catalog, provenance file, block snapshot |
| `scripts/release_gate.py` | Runs the suite, prints verdict, exits non-zero on failure |

## Architecture

```text
app/
  main.py                  FastAPI entrypoint (health, capabilities, agents, workflows)
  actions/                 Capability actions (kernel ActionOutcome pattern)
  agents/manifests/        Agent hat manifests
  workflows/               Workflow definitions
  cerebrum_product_kernel/ Vendored kernel contract
  connectors/              Honest stub connectors (not_implemented)
vendor/blocks/             Versioned Store block snapshot (offline-verifiable)
product-dna/               Entity model, rule overlay, test catalog, block lockfile
docs/
  blueprint/               The blueprint this repo was generated from
  provenance/              factory/blocks commits + inputs hash
  certification/           Dual certification scaffold (pending until certified)
frontend/                  Generated UI shell (modules per ui_modules)
scripts/release_gate.py    The clone-and-test verdict script
tests/                     Smoke suite executed by the gate
```

## Feature-to-block map

| Capability | Strategy | Store blocks |
| --- | --- | --- |
{feature_table}

## Honesty notes

{honesty_block}

## Provenance

See `docs/provenance/provenance.json` and `factory_plan.json`.
"""
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
        (tests_dir / "test_smoke.py").write_text(
            f'''"""Smoke suite for {bp.product_name} — run by scripts/release_gate.py.

Proves the generated pilot is alive and self-describing without any
external service. Extend per product; never delete the provenance check.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).resolve().parents[1]
PRODUCT_ID = "{bp.product_id}"
EXPECTED_CAPABILITIES = {cap_ids!r}

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

    catalog_ids = {{a["capability_id"] for a in ACTION_CATALOG}}
    for cap_id in EXPECTED_CAPABILITIES:
        assert cap_id in catalog_ids, f"capability {{cap_id}} missing from action catalog"


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
''',
            encoding="utf-8",
        )

        scripts_dir = out / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        (scripts_dir / "release_gate.py").write_text(
            f'''#!/usr/bin/env python3
"""Release gate for {bp.product_name}.

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
    print("== {bp.product_name} — release gate ==")
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
''',
            encoding="utf-8",
        )

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

        (app / "main.py").write_text(
            f'''"""Generated FastAPI entrypoint for {self.blueprint.product_id}."""

from fastapi import FastAPI

app = FastAPI(title="{self.blueprint.product_name}", version="1.0.0")


@app.get("/health")
def health():
    return {{
        "ok": True,
        "product_id": "{self.blueprint.product_id}",
        "vertical": "{self.blueprint.vertical}",
        "human_authority": {str(self.blueprint.human_authority)},
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


# Estate kit surfaces (demo fixtures + dual RAG) are mounted when present.
try:
    from app.estate_kit import router as estate_kit_router

    app.include_router(estate_kit_router)
except ImportError:
    pass

# Production Steward RAG (Postgres/pgvector + packs) when generated.
try:
    from app.steward.api import router as steward_rag_router

    app.include_router(steward_rag_router)
except ImportError:
    pass
''',
            encoding="utf-8",
        )

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
                json.dumps({{"properties": [], "honesty": "fixtures missing at generate"}}, indent=2)
                + "\n",
                encoding="utf-8",
            )

        dual_rag = {{
            "schema_version": "1.1.0",
            "layers": {{
                "1": {{
                    "id": "sop_standards",
                    "name": "SOP / House Manual / global standards",
                    "index": "steward_sop_v1",
                    "project": "prebuilt_steward_core",
                    "tenant": "platform",
                    "source_field": "sop_corpus",
                    "blocks": ["document_engine", "knowledge", "vector_search", "database"],
                }},
                "2": {{
                    "id": "estate_documents",
                    "name": "Estate documents (separately indexed)",
                    "index": "steward_estate_docs_v1",
                    "source_field": "estate_documents",
                    "blocks": ["document_engine", "knowledge", "vector_search", "database"],
                }},
            }},
            "demo_path": {{
                "embedding_provider": "local_feature_hash_v1",
                "vector_adapter": "local_flat_json_v1",
                "routes": ["/v1/rag/query", "/v1/rag/ingest", "/v1/rag/dual"],
                "notes": "Zero-deps CI/local fallback when STEWARD_DATABASE_URL is unset.",
            }},
            "pilot_path": {{
                "embedding_provider": "fastembed:BAAI/bge-small-en-v1.5",
                "vector_adapter": "postgres_jsonb_v1",
                "routes": ["/v1/rag/query", "/v1/rag/ingest", "/v1/rag/dual", "/v1/rag/bootstrap"],
                "env": [
                    "STEWARD_DATABASE_URL",
                    "STEWARD_EMBED_BACKEND=fastembed",
                    "STEWARD_REQUIRE_PRODUCTION_EMBEDDINGS=1",
                    "STEWARD_REQUIRE_PERSISTENT_RAG=1",
                ],
            }},
            "production_path": {{
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
            }},
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
        }}
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
            "export const DEEP_LINKS = {{\n"
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
            "}} as const;\n"
            "\n"
            "/** Cold-start retry hint for Render free-tier spin-up. */\n"
            "export const COLD_START = {{\n"
            "  retries: 3,\n"
            "  backoffMs: [1000, 3000, 8000],\n"
            "  message: 'Service waking up — retrying…',\n"
            "}};\n",
            encoding="utf-8",
        )

    def _write_runtime_packaging(self, out: Path) -> None:
        """Emit Dockerfile + Procfile so generated products are Render-deployable."""
        docker_lines = [
            "FROM python:3.12-slim",
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
                "EXPOSE 8000",
                'CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]',
            ]
        )
        (out / "Dockerfile").write_text("\n".join(docker_lines) + "\n", encoding="utf-8")
        (out / "Procfile").write_text(
            "web: uvicorn app.main:app --host 0.0.0.0 --port ${{PORT:-8000}}\n",
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
        actions_dir = out / "app" / "actions"
        actions_dir.mkdir(parents=True, exist_ok=True)
        specs = []
        for cap in self.plan.capabilities:
            action_name = cap.capability_id.replace("-", "_")
            domain = self.blueprint.vertical.replace("-", "_")
            # action_id must be domain.action
            action_id = f"{{domain}}.{{action_name}}"
            specs.append(
                {{
                    "action_id": action_id,
                    "capability_id": cap.capability_id,
                    "strategy": cap.strategy,
                    "block_ids": cap.block_ids,
                    "notes": cap.notes,
                }}
            )
            mod = actions_dir / f"{{action_name}}.py"
            blocks_list = ", ".join(cap.block_ids) or "(none — kernel/GENERATE)"
            mod.write_text(
                f'''"""Generated action module for {{action_id}} (strategy={{cap.strategy}}).

Uses cerebrum_product_kernel ActionOutcome patterns. Durable logic belongs in
Factory templates / dual-registered blocks — regenerate rather than hand-edit.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.cerebrum_product_kernel.contract.models import (
    ActionEvidence,
    ActionOutcome,
    ActionStatus,
)

ACTION_ID = "{{action_id}}"
CAPABILITY_ID = "{{cap.capability_id}}"
STRATEGY = "{{cap.strategy}}"
BLOCK_IDS: List[str] = {{cap.block_ids!r}}


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

    evidence = [
        ActionEvidence(
            source_id=bid,
            filename=f"block:{{bid}}",
            excerpt=f"Resolved via dual-registered block {{bid}}",
            metadata={{{{"strategy": STRATEGY}}}},
        )
        for bid in BLOCK_IDS
    ]
    output = {{
        "action_id": ACTION_ID,
        "capability_id": CAPABILITY_ID,
        "strategy": STRATEGY,
        "block_ids": BLOCK_IDS,
        "arguments": arguments or {{}},
        "tenant_id": tenant_id,
        "result": {{
            "ok": True,
            "summary": f"{{CAPABILITY_ID}} executed via Factory template ({{STRATEGY}})",
            "blocks_used": BLOCK_IDS,
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
            index.append({{"agent_id": m["agent_id"], "kind": m["kind"], "file": fname}})
        (out / "app" / "agents" / "__init__.py").write_text(
            "HAT_INDEX = " + json.dumps(index, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return manifests

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
            (modules_dir / f"{{safe}}.tsx").write_text(
                f"""/* Generated UI module: {{mod}} — Factory template, regenerate-only. */
import {{ useEffect, useState }} from "react";

const MODULE_ID = "{{mod}}";
const PRODUCT_ID = "{self.blueprint.product_id}";
const CAPABILITIES = {{json.dumps(cap_ids)}};

export default function {{component}}() {{
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
            f"import {{re.sub(r'[^a-zA-Z0-9_]', '_', m).title().replace('_', '')}}Module "
            f"from './modules/{{re.sub(r'[^a-zA-Z0-9_]', '_', m)}}';"
            for m in modules
        )
        renders = "\n".join(
            f"      <{{re.sub(r'[^a-zA-Z0-9_]', '_', m).title().replace('_', '')}}Module />"
            for m in modules
        )
        (ui / "App.tsx").write_text(
            f"/* Generated UI shell for {self.blueprint.product_name} */\n"
            f"{{imports}}\n"
            f"export default function App(){{{{\n"
            f"  return (\n"
            f"    <main data-product=\"{self.blueprint.product_id}\">\n"
            f"      <h1>{self.blueprint.product_name}</h1>\n"
            f"      <p>{self.blueprint.summary}</p>\n"
            f"{{renders}}\n"
            f"    </main>\n"
            f"  )\n"
            f"}}}}\n",
            encoding="utf-8",
        )
        (out / "frontend" / "package.json").write_text(
            json.dumps(
                {{"name": self.blueprint.product_id, "private": True, "version": "1.0.0"}},
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
            items.append({{"id": c, "status": "not_implemented", "honest": True}})
            (conn / f"{{c}}.py").write_text(
                f'"""Honest stub connector: {{c}}."""\n\nSTATUS = "not_implemented"\n',
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

    def _write_blueprint_copy(self, out: Path) -> None:
        docs = out / "docs" / "blueprint"
        docs.mkdir(parents=True, exist_ok=True)
        (docs / "product_blueprint.json").write_text(
            json.dumps(blueprint_to_dict(self.blueprint), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _write_edge_profile(self, out: Path) -> None:
        profile = {{
            "edge_profile": self.blueprint.edge_profile,
            "human_authority": self.blueprint.human_authority,
            "isolation": "property",
            "pii_in_learning": False,
        }}
        (out / "docs" / "edge_profile.json").write_text(
            json.dumps(profile, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def _write_certification_scaffold(self, out: Path) -> None:
        cert = out / "docs" / "certification"
        cert.mkdir(parents=True, exist_ok=True)
        report = {{
            "PRODUCT_CERTIFIED": "pending",
            "RESIDENT_AGENT_CERTIFIED": "pending",
            "incomplete_until_both": True,
            "factory_scenario": self.blueprint.factory_scenario.value,
        }}
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
