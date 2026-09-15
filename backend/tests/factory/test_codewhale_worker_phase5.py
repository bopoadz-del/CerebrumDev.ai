"""Phase 5 acceptance: the CodeWhale writer worker.

T5.1 headless exec (runs where the CLI exists, skips with a reason
elsewhere); T5.2 worker under the tenant store; T5.3 concurrency cap;
T5.6 deterministic prompt fill; P5 unbound-worker mutation.
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path

import pytest

from app.factory.build.codewhale_worker import (
    NO_AUTHENTICATED_TENANT,
    WORKER_CLI_MISSING,
    WORKER_CONCURRENCY_CAPPED,
    WorkerError,
    run_worker_job,
    worker_cap,
)
from app.factory.build.writer_prompt import PROMPT_VERSION, render_writer_prompt

_CLI = shutil.which("codewhale")


class _FakeStore:
    """Stands in for a Phase 1 TenantStore handle in worker-layer tests."""

    def __init__(self, digest: str = "d1"):
        self.digest = digest
        self.sqlite_path = Path(f"/tmp/tenant-{digest}/platform.db")
        self.chroma_collection = f"tenant_{digest}"
        self.storage_root = Path(f"/tmp/tenant-{digest}")
        self.tenant_id = f"tenant_{digest}"


# -- T5.2: the worker runs under the tenant store, never unbound ------------


def test_worker_refuses_an_unbound_tenant_store():
    with pytest.raises(WorkerError) as exc:
        run_worker_job("build anything", "/tmp/anywhere", tenant_store=None)
    assert NO_AUTHENTICATED_TENANT in str(exc.value)


# -- T5.3: the concurrency cap is a hard refusal ------------------------------


def test_concurrency_cap_enforced(monkeypatch):
    monkeypatch.setenv("FACTORY_CODEWHALE_WORKER_CAP", "1")
    assert worker_cap() == 1

    from app.factory.build import codewhale_worker as worker_mod

    released = threading.Event()

    def hold_slot():
        with worker_mod.worker_job_slot(_FakeStore("a")):
            released.set()
            # Hold the slot until the main thread has tried the second job.
            threading.Event().wait(timeout=5)

    holder = threading.Thread(target=hold_slot)
    holder.start()
    released.wait(timeout=5)

    with pytest.raises(WorkerError) as exc:
        with worker_mod.worker_job_slot(_FakeStore("b")):
            pass
    assert WORKER_CONCURRENCY_CAPPED in str(exc.value)

    holder.join(timeout=10)
    # After the holder releases, a slot is available again.
    with worker_mod.worker_job_slot(_FakeStore("c")):
        pass


# -- T5.6: the prompt template is deterministic -------------------------------


class _Blueprint:
    product_id = "bp-1"
    product_name = "Probe Platform"
    vertical = "probe"
    summary = "deterministic probe"


def test_prompt_template_is_deterministic():
    first = render_writer_prompt(_Blueprint(), brief="build me a probe")
    second = render_writer_prompt(_Blueprint(), brief="build me a probe")
    assert first == second
    assert PROMPT_VERSION in first
    assert "bp-1" in first
    assert "build me a probe" in first


def test_prompt_version_is_the_product():
    text = render_writer_prompt(_Blueprint(), brief="x")
    assert text.startswith(f"<!-- {PROMPT_VERSION} -->")


# -- T5.1: headless exec, verified where the CLI exists -----------------------


@pytest.mark.skipif(
    _CLI is None, reason="codewhale CLI not installed — headless probe is CI-gated"
)
def test_codewhale_exec_headless(tmp_path):
    """The worker completes a trivial job with no interactive prompt.

    This is the Phase 5.1 verification: agent mode, JSON summary,
    termination_reason resolved. Runs wherever codewhale exists; CI has no
    binary and skips it with a reason (surfaced by -rs).
    """
    receipt = run_worker_job(
        "Create a file named probe.txt containing the text: headless-ok",
        tmp_path,
        tenant_store=_FakeStore("t51"),
        timeout_s=300,
    )
    assert receipt.status == "completed"
    assert (tmp_path / "probe.txt").read_text(encoding="utf-8").strip() == "headless-ok"


def test_worker_cli_missing_is_a_named_refusal(monkeypatch):
    monkeypatch.setattr(
        "app.factory.build.codewhale_worker.worker_cli_path", lambda: None
    )
    with pytest.raises(WorkerError) as exc:
        run_worker_job("x", "/tmp/x", tenant_store=_FakeStore("d2"))
    assert WORKER_CLI_MISSING in str(exc.value)


def test_headless_dispatch_passes_provider_and_key_from_env(monkeypatch, tmp_path):
    """On Render the CLI authenticates with explicit flags; without the env
    key the CLI falls back to its own stored config (local dev)."""
    import subprocess

    from app.factory.build import codewhale_worker as worker_mod

    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return subprocess.CompletedProcess(
            argv, 0, stdout='{"status": "completed", "termination_reason": "resolved"}', stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(worker_mod, "worker_cli_path", lambda: "/usr/local/bin/codewhale")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-test")
    monkeypatch.delenv("CODEWHALE_PROVIDER", raising=False)

    receipt = run_worker_job("build it", tmp_path, tenant_store=_FakeStore("d3"))
    assert receipt.status == "completed"
    argv = captured["argv"]
    assert argv[0].endswith("codewhale")
    assert argv[1:4] == ["exec", "--auto", "--json"]
    assert "--provider" in argv and "deepseek" in argv
    assert "--api-key" in argv and "sk-deepseek-test" in argv


def test_no_env_key_falls_back_to_cli_config(monkeypatch, tmp_path):
    import subprocess

    from app.factory.build import codewhale_worker as worker_mod

    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return subprocess.CompletedProcess(
            argv, 0, stdout='{"status": "completed"}', stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(worker_mod, "worker_cli_path", lambda: "/usr/local/bin/codewhale")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("CODEWHALE_API_KEY", raising=False)

    run_worker_job("build it", tmp_path, tenant_store=_FakeStore("d4"))
    argv = captured["argv"]
    assert "--api-key" not in argv
    assert argv[-1] == "build it"
