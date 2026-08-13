"""The generated suite must never share state with the build environment.

New-shape test for the stale-database defect found on the first live coder
build. The factory backend's own environment carries STORAGE_PATH (loaded
from backend/.env by the CLI), the tester subprocess inherits it, and the
generated conftest used ``setdefault`` -- so every rework round ran against
one shared database file. A table created by round N rejected round N+1's
columns (``no column named assigned_by``) and the rework loop spent its
whole budget on schema errors that no round's code had caused.
"""

from __future__ import annotations

from app.factory.build.roles import _CONFTEST


def test_generated_conftest_forces_storage_isolation(tmp_path, monkeypatch):
    """Exec the conftest with STORAGE_PATH already set, the way the tester
    subprocess actually runs it. The inherited value must lose."""
    inherited = str(tmp_path / "inherited-storage")
    monkeypatch.setenv("STORAGE_PATH", inherited)

    namespace: dict = {"__file__": str(tmp_path / "tests" / "conftest.py")}
    (tmp_path / "tests").mkdir()
    exec(compile(_CONFTEST, "conftest.py", "exec"), namespace)

    import os

    forced = os.environ["STORAGE_PATH"]
    assert forced != inherited, "the generated suite inherited the build's storage"
    assert "platform-test-" in forced


def test_generated_conftest_blocks_outbound_network(tmp_path):
    """A round-12 handler "sent" a webhook to the open internet from a suite
    whose whole claim is offline; env-stripping cannot catch that. The
    conftest must refuse non-loopback connections and keep loopback open.
    Run in a subprocess: the guard patches socket globally."""
    import subprocess
    import sys
    import textwrap

    from app.factory.build.roles import _CONFTEST

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "conftest.py").write_text(_CONFTEST, encoding="utf-8")

    probe = textwrap.dedent(
        """
        import runpy, socket, sys
        sys.argv = ["probe"]
        namespace = runpy.run_path("tests/conftest.py")

        # Outbound must be refused before any packet leaves.
        try:
            socket.create_connection(("93.184.216.34", 80), timeout=5)
        except OSError as exc:
            assert "offline suite" in str(exc), exc
        else:
            raise SystemExit("outbound connection was allowed")

        # Loopback must still work (TestClient-style local servers).
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        client = socket.socket()
        client.connect(("127.0.0.1", listener.getsockname()[1]))
        client.close()
        listener.close()
        print("OFFLINE-GUARD-OK")
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "OFFLINE-GUARD-OK" in proc.stdout


def test_gate_subprocesses_run_utf8(tmp_path):
    """A vendored block printing one checkmark died with a charmap
    UnicodeEncodeError under the Windows console codepage -- looking exactly
    like a block failure. Gate subprocesses must be UTF-8 regardless of
    platform."""
    import sys

    from app.factory.build.gates import _real_run

    proc = _real_run(
        [sys.executable, "-c", "print('\\u2713 ok')"],
        cwd=tmp_path,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout
