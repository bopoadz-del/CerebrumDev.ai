"""Regression coverage for the delivery-path fixes.

Two live-factory failures, one root each:

1. The headless CodeWhale worker was dispatched as
   ``codewhale exec --auto --json --provider deepseek --api-key ...`` —
   codewhale 0.9.13 parses ``--provider``/``--api-key`` as GLOBAL flags
   and refuses them after ``exec`` ("--provider must be placed before
   `exec`"), so the WRITER died in under a second and authored nothing.

2. The TESTER harness generated ``tests/test_models.py`` with a bare
   ``def test_every_model_round_trips():`` when a plan declared
   capabilities but no on-disk handler produced model specs — an
   IndentationError that burned the rework budget and masked the real
   gap ("No module named 'app'").
"""

from __future__ import annotations

import ast
import json
import subprocess
from types import SimpleNamespace
from unittest import mock

from app.factory.build import codewhale_worker
from app.factory.build.authority import BuildRole
from app.factory.build.roles_handlers import run_tester
from app.factory.build.roles_models import RoleContext
from app.factory.build.workspace import RoleWorkspace


def test_worker_argv_puts_provider_and_api_key_before_exec(tmp_path):
    """Global flags precede the `exec` subcommand (codewhale 0.9.13 usage)."""
    prompt = "write the platform"
    captured: dict = {}

    def fake_run(argv, *, cwd, capture_output, text, timeout):
        captured["argv"] = argv
        captured["cwd"] = cwd
        return subprocess.CompletedProcess(
            args=argv,
            returncode=0,
            stdout=json.dumps(
                {"status": "completed", "termination_reason": "resolved"}
            ),
            stderr="",
        )

    with mock.patch.object(codewhale_worker, "worker_cli_path", return_value="codewhale"), \
         mock.patch.object(codewhale_worker, "worker_api_key", return_value="sk-test"), \
         mock.patch.object(codewhale_worker, "worker_provider", return_value="deepseek"), \
         mock.patch.object(codewhale_worker, "worker_job_slot", side_effect=mock.MagicMock()), \
         mock.patch.object(codewhale_worker.subprocess, "run", side_effect=fake_run):
        receipt = codewhale_worker.run_worker_job(
            prompt, tmp_path / "checkout", tenant_store=object()
        )

    assert receipt.status == "completed"
    assert captured["argv"] == [
        "codewhale",
        "--provider",
        "deepseek",
        "--api-key",
        "sk-test",
        "exec",
        "--auto",
        "--json",
        prompt,
    ]
    # Global flags must never be appended after the subcommand again.
    assert captured["argv"].index("--provider") < captured["argv"].index("exec")


def test_writer_uses_codewhale_falls_back_to_process_env(monkeypatch):
    """The runner invokes role handlers with ctx only — env is always None
    in production — so the worker switch must read os.environ (the same
    fallback writer_uses_cli_pivot applies). Without it the seam silently
    never arms (live-factory failure sess_b9db05967cb94e6f: writer_no_output,
    28 templated files, zero agent-authored)."""
    from app.factory.build.roles_handlers import writer_uses_codewhale

    monkeypatch.setenv("FACTORY_CODEWHALE_WRITER", "1")
    assert writer_uses_codewhale(None) is True
    monkeypatch.setenv("FACTORY_CODEWHALE_WRITER", "0")
    assert writer_uses_codewhale(None) is False


def test_tester_harness_models_file_compiles_with_no_specs(tmp_path):
    """Capabilities declared but zero handler specs must still yield a
    syntactically valid tests/test_models.py (bare-def regression)."""
    ctx = RoleContext(
        role=BuildRole.TESTER,
        workspace=RoleWorkspace("TESTER", tmp_path),
        blueprint=SimpleNamespace(
            product_id="automotive",
            product_name="AutoDealer Hub",
            vertical="automotive",
        ),
        plan=SimpleNamespace(
            capabilities=[SimpleNamespace(capability_id="automotive_core")]
        ),
        state={"model_specs": {}, "route_bodies": {}, "vendored_blocks": ()},
    )
    result = run_tester(ctx)
    assert result.ok, result.detail

    models_src = (tmp_path / "tests" / "test_models.py").read_text(encoding="utf-8")
    ast.parse(models_src)  # must not raise
    assert "def test_every_model_round_trips():" in models_src
    assert "    pass" in models_src, "round-trip test must have a body"

    # Every other generated harness file must parse too — a red suite that
    # cannot even collect hides the real gap from the rework round.
    for test_file in sorted((tmp_path / "tests").glob("test_*.py")):
        ast.parse(test_file.read_text(encoding="utf-8"))


def test_tester_harness_models_file_compiles_with_specs(tmp_path):
    """The populated path stays valid: one capability with sampleable fields."""
    spec = {
        "entity": "vehicle",
        "fields": [
            {"name": "make", "type": "str"},
            {"name": "year", "type": "int"},
        ],
    }
    ctx = RoleContext(
        role=BuildRole.TESTER,
        workspace=RoleWorkspace("TESTER", tmp_path),
        blueprint=SimpleNamespace(
            product_id="automotive",
            product_name="AutoDealer Hub",
            vertical="automotive",
        ),
        plan=SimpleNamespace(
            capabilities=[SimpleNamespace(capability_id="automotive_core")]
        ),
        state={
            "model_specs": {"automotive_core": spec},
            "route_bodies": {},
            "vendored_blocks": (),
        },
    )
    result = run_tester(ctx)
    assert result.ok, result.detail

    models_src = (tmp_path / "tests" / "test_models.py").read_text(encoding="utf-8")
    ast.parse(models_src)
    assert "record = {'make':" in models_src
    assert "def test_models_expose_their_fields():" in models_src
