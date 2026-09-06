"""Level grade is fail-closed: pilot_ready false cannot read as founding."""

from __future__ import annotations

from pathlib import Path

from app.factory.build.converge import FOURTEEN_ARTIFACT_CLASSES
from app.factory.build.level_grade import (
    Level,
    parse_three_gate_verdict,
    grade_workspace,
)
from app.factory.build.product_gate import GATE_SCOPES


def test_verdict_parser_reads_the_three_named_gates():
    gates = parse_three_gate_verdict(
        "CODE PASS — imports; PRODUCT NOT RUN — round-trip; STORE NOT RUN — restart"
    )
    assert gates == {"CODE": "PASS", "PRODUCT": "NOT_RUN", "STORE": "NOT_RUN"}
    assert set(GATE_SCOPES) == {"CODE", "PRODUCT", "STORE"}


def test_code_cycle_success_is_code_green_not_founding(tmp_path):
    status = {
        "state": "succeeded",
        "cycle": "code",
        "pilot_ready": False,
        "detail": (
            "CODE PASS — the code-phase suite; "
            "PRODUCT NOT RUN — post-boot; "
            "STORE NOT RUN — publish"
        ),
    }
    grade = grade_workspace(tmp_path, status=status)
    assert grade["level"] == Level.CODE_GREEN.value
    assert grade["founding_customer_ready"] is False
    assert grade["pilot_ready"] is False
    assert any("pilot_ready is false" in b for b in grade["blockers"])


def test_pilot_ready_false_cannot_become_store_green_even_if_verdict_overclaims(tmp_path):
    """Honesty lock. Mutation killed: grading STORE_GREEN off the sentence alone."""
    status = {
        "state": "succeeded",
        "cycle": "pilot",
        "pilot_ready": False,
        "detail": "CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
    }
    grade = grade_workspace(tmp_path, status=status)
    assert grade["level"] not in {
        Level.STORE_GREEN.value,
        Level.FOUNDING_CUSTOMER_READY.value,
    }
    assert grade["founding_customer_ready"] is False


def test_failed_build_is_scaffold(tmp_path):
    grade = grade_workspace(
        tmp_path,
        status={
            "state": "failed",
            "cycle": "pilot",
            "pilot_ready": False,
            "detail": "CODE PASS — x; PRODUCT FAIL — suite is red; STORE NOT RUN — z",
        },
    )
    assert grade["level"] == Level.SCAFFOLD.value
    assert grade["three_gate"]["PRODUCT"] == "FAIL"


def _full_repo(root: Path) -> None:
    for rel in FOURTEEN_ARTIFACT_CLASSES:
        path = root / rel
        if rel.endswith(".py") or "." in Path(rel).name:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# ok\n", encoding="utf-8")
        else:
            path.mkdir(parents=True, exist_ok=True)
            (path / ".keep").write_text("", encoding="utf-8")
    extras = {
        "Dockerfile": "FROM python:3.12-slim\n",
        "README.md": "pilot\n",
        "requirements.txt": "fastapi\n",
        "app/block_inputs.py": "def prepare_block_input(*a, **k):\n    return {}\n",
        "tests/test_routes.py": (
            "def test_every_capability_route_accepts_payload():\n    assert True\n"
        ),
        "frontend/src/App.tsx": "export default function App() { return null }\n",
        "app/actions/unit_registry_and_vacancy_tracking.py": (
            "def handle(payload):\n    return {'ok': True}\n"
        ),
    }
    for rel, body in extras.items():
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(body, encoding="utf-8")


def test_full_repo_with_pilot_ready_is_founding(tmp_path):
    _full_repo(tmp_path)
    grade = grade_workspace(
        tmp_path,
        status={
            "state": "succeeded",
            "cycle": "pilot",
            "pilot_ready": True,
            "detail": (
                "CODE PASS — the code-phase suite; "
                "PRODUCT PASS — round-trip; "
                "STORE PASS — restart"
            ),
        },
    )
    assert grade["level"] == Level.FOUNDING_CUSTOMER_READY.value
    assert grade["founding_customer_ready"] is True
    assert grade["blockers"] == []


def test_cli_billing_keep_path_is_store_green_not_founding(tmp_path):
    """#338 keep-path: PRODUCT/STORE pass, but CLI billing is not founding."""
    _full_repo(tmp_path)
    grade = grade_workspace(
        tmp_path,
        status={
            "state": "succeeded",
            "cycle": "pilot",
            "pilot_ready": True,
            "detail": (
                "CODE PASS — the code-phase suite; "
                "PRODUCT PASS — round-trip; "
                "STORE PASS — restart"
            ),
            "authorship": {"artifacts": 24, "agent_written": 1, "templated": 23},
            "coder_receipt": {
                "ok": False,
                "blocker": "FACTORY_CODE_CLI_BILLING",
                "honesty_class": "FACTORY_CODE_CLI_FAILED",
                "detail": "FACTORY_CODE_CLI_BILLING: 429 — insufficient balance",
            },
        },
    )
    assert grade["founding_customer_ready"] is False
    assert grade["level"] == Level.STORE_GREEN.value
    assert grade["pilot_ready"] is True
    assert any("FACTORY_CODE_CLI_BILLING" in b or "templated" in b for b in grade["blockers"])


def test_template_majority_authorship_is_store_green_not_founding(tmp_path):
    """sess_45729 0639: 8/16 factory-LLM thin zip is Store-green, not founding."""
    _full_repo(tmp_path)
    grade = grade_workspace(
        tmp_path,
        status={
            "state": "succeeded",
            "cycle": "pilot",
            "pilot_ready": True,
            "detail": (
                "CODE PASS — the code-phase suite; "
                "PRODUCT PASS — round-trip; "
                "STORE PASS — restart"
            ),
            "authorship": {"artifacts": 24, "agent_written": 8, "templated": 16},
        },
    )
    assert grade["founding_customer_ready"] is False
    assert grade["level"] == Level.STORE_GREEN.value
    assert grade["pilot_ready"] is True
    assert any("templated" in b for b in grade["blockers"])


def test_factory_llm_fallthrough_blocks_founding_without_cli_name(tmp_path):
    _full_repo(tmp_path)
    grade = grade_workspace(
        tmp_path,
        status={
            "state": "succeeded",
            "cycle": "pilot",
            "pilot_ready": True,
            "detail": "CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
            "authorship": {"artifacts": 28, "agent_written": 22, "templated": 6},
            "coder_receipt": {
                "ok": True,
                "factory_llm_generate_fallthrough": True,
                "factory_llm_written_ids": ["veterinary_care_core"],
            },
        },
    )
    assert grade["founding_customer_ready"] is False
    assert grade["level"] == Level.STORE_GREEN.value
    assert any("factory_llm_generate_fallthrough" in b for b in grade["blockers"])


def test_http_store_callback_blocks_founding(tmp_path):
    _full_repo(tmp_path)
    (tmp_path / "app" / "actions" / "viewing_management.py").write_text(
        "import httpx\nurl = store_url + '/v1/execute'\n",
        encoding="utf-8",
    )
    grade = grade_workspace(
        tmp_path,
        status={
            "state": "succeeded",
            "cycle": "pilot",
            "pilot_ready": True,
            "detail": "CODE PASS — x; PRODUCT PASS — y; STORE PASS — z",
        },
    )
    assert grade["founding_customer_ready"] is False
    assert grade["level"] == Level.STORE_GREEN.value
    assert any("httpx" in b or "store" in b for b in grade["blockers"])
