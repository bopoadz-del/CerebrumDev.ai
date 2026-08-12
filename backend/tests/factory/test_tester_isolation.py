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
