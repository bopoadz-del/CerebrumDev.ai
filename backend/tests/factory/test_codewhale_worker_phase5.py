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
    PROCESS_CAP_ENV,
    PROCESS_SLOTS_EXHAUSTED,
    PROFILE_ENV,
    TENANT_CAP_ENV,
    WORKER_CLI_MISSING,
    WORKER_CONCURRENCY_CAPPED,
    InProcessSlotCounter,
    WorkerError,
    run_worker_job,
    set_slot_counter,
    worker_cap,
    worker_slots_snapshot,
)
from app.factory.build.tenant_bind import TenantStoreBinding
from app.factory.build.writer_prompt import PROMPT_VERSION, render_writer_prompt

_CLI = shutil.which("codewhale")


def _binding(digest: str = "d1") -> TenantStoreBinding:
    """A handle shaped exactly like the one production binds.

    This used to be a class carrying digest/sqlite_path/chroma_collection/
    tenant_id and NO ``tenant_key`` — which is not the handle the runner
    threads to the worker. Slot accounting keys on ``tenant_key``, so a
    double without one proves nothing about the real path.
    """
    return TenantStoreBinding(
        tenant_key=f"key_{digest}",
        store_dir=Path(f"/tmp/tenant-{digest}"),
        bound_at=0.0,
    )


@pytest.fixture(autouse=True)
def _fresh_slots(monkeypatch):
    """Slot state never leaks between tests in this module."""
    for name in (PROCESS_CAP_ENV, TENANT_CAP_ENV, PROFILE_ENV):
        monkeypatch.delenv(name, raising=False)
    previous = set_slot_counter(InProcessSlotCounter())
    try:
        yield
    finally:
        set_slot_counter(previous)


# -- T5.2: the worker runs under the tenant store, never unbound ------------


def test_worker_refuses_an_unbound_tenant_store():
    with pytest.raises(WorkerError) as exc:
        run_worker_job("build anything", "/tmp/anywhere", tenant_store=None)
    assert NO_AUTHENTICATED_TENANT in str(exc.value)


# -- T5.3: the concurrency cap is a hard refusal ------------------------------


def test_process_cap_refuses_when_the_instance_is_full(monkeypatch):
    """The surviving host-protection half of the old T5.3.

    The old test held a slot as tenant "a" and asserted tenant "b" was
    REFUSED — cross-tenant starvation written down as expected behaviour,
    and the reason the bug shipped green. What is genuinely true is
    narrower: when the INSTANCE is full, the next job is refused whoever it
    belongs to, and the message says so. The per-tenant half now lives in
    test_codewhale_worker_concurrency.py.
    """
    monkeypatch.setenv(PROCESS_CAP_ENV, "1")
    monkeypatch.setenv(TENANT_CAP_ENV, "1")
    assert worker_cap() == 1

    from app.factory.build import codewhale_worker as worker_mod

    holding = threading.Event()
    release_holder = threading.Event()
    failed: list = []

    def hold_slot():
        try:
            with worker_mod.worker_job_slot(_binding("a")):
                holding.set()
                # Released by an explicit signal, not by a blind sleep: the
                # old fresh-Event().wait(timeout=5) burned five wall-clock
                # seconds on every run and left the "slot is free again"
                # assertion racing the join instead of sequenced by it.
                assert release_holder.wait(timeout=30), "holder never released"
        except Exception as exc:  # pragma: no cover - surfaced below
            failed.append(exc)
            holding.set()

    holder = threading.Thread(target=hold_slot, name="t53-holder")
    holder.start()
    assert holding.wait(timeout=30)
    assert not failed, failed

    with pytest.raises(WorkerError) as exc:
        with worker_mod.worker_job_slot(_binding("b")):
            pass
    message = str(exc.value)
    assert WORKER_CONCURRENCY_CAPPED in message
    assert PROCESS_SLOTS_EXHAUSTED in message

    release_holder.set()
    holder.join(timeout=30)
    assert not holder.is_alive()
    assert not failed, failed
    assert worker_slots_snapshot() == {"total": 0, "by_tenant": {}}

    # After the holder releases, a slot is available again.
    with worker_mod.worker_job_slot(_binding("c")):
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
        tenant_store=_binding("t51"),
        timeout_s=300,
    )
    assert receipt.status == "completed"
    assert (tmp_path / "probe.txt").read_text(encoding="utf-8").strip() == "headless-ok"


def test_worker_cli_missing_is_a_named_refusal(monkeypatch):
    monkeypatch.setattr(
        "app.factory.build.codewhale_worker.worker_cli_path", lambda: None
    )
    with pytest.raises(WorkerError) as exc:
        run_worker_job("x", "/tmp/x", tenant_store=_binding("d2"))
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

    receipt = run_worker_job("build it", tmp_path, tenant_store=_binding("d3"))
    assert receipt.status == "completed"
    argv = captured["argv"]
    assert argv[0].endswith("codewhale")
    # codewhale 0.9.13 parses --provider/--api-key as GLOBAL flags: they
    # must precede the exec subcommand. After the subcommand the CLI
    # refuses them and the WRITER dies in under a second authoring
    # nothing (live-factory failure sess_d5a7f55b8a9c4dad).
    assert argv.index("--provider") < argv.index("exec")
    assert argv[argv.index("--provider") + 1] == "deepseek"
    assert argv.index("--api-key") < argv.index("exec")
    assert argv[argv.index("exec") + 1 : argv.index("exec") + 3] == [
        "--auto",
        "--json",
    ]
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

    run_worker_job("build it", tmp_path, tenant_store=_binding("d4"))
    argv = captured["argv"]
    assert "--api-key" not in argv
    assert argv[-1] == "build it"
