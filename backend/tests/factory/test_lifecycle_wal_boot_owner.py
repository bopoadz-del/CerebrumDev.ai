"""The emitted WAL check asserts the persistence contract the Factory declares.

Live 2026-10-07 (9de69276 vineyard repro): TESTER failed
``test_connect_is_wal_with_a_busy_timeout_and_creates_nothing`` with
``assert ('delete' == 'wal'``. The product's store.connect() routed through
the Factory's app.db (busy_timeout, no per-connection mode switch) and WAL
was set once at boot by the Factory's app.migrations.upgrade_head() -- the
Factory's own design. The emitted test demanded WAL on a bare connect() or a
store-level enable_wal(), and the writer prompt told the writer connect()
sets WAL: two Factory statements against the Factory's own substrate.

The declared contract is now the substrate's: WAL after the boot path, a
busy_timeout and no table on a bare connect(). WAL is still required.
"""

from __future__ import annotations

import sqlite3
import textwrap
from pathlib import Path

from app.factory.build.data_lifecycle import render_product_tests

_SPECS = {"tank_log": {"entity": "tank_log", "fields": [{"name": "label", "type": "str"}]}}
_NAME = "test_connect_is_wal_with_a_busy_timeout_and_creates_nothing"


def _emitted_test_source() -> str:
    suite = render_product_tests(_SPECS)
    start = suite.index(f"def {_NAME}(")
    end = suite.index("\ndef ", start + 1)
    return suite[start:end]


def _store_module(tmp_path: Path, db: Path) -> object:
    """A writer-authored store shaped like the live one: connect() sets only a
    busy_timeout and exposes no enable_wal()."""
    src = textwrap.dedent(
        f"""
        import sqlite3

        def connect():
            conn = sqlite3.connect({str(db)!r})
            conn.execute("PRAGMA busy_timeout=30000")
            return conn
        """
    )
    path = tmp_path / "live_store.py"
    path.write_text(src, encoding="utf-8")
    ns: dict = {"__file__": str(path)}
    exec(compile(src, str(path), "exec"), ns)
    module = type(textwrap)("live_store")
    module.__dict__.update(ns)
    module.__file__ = str(path)
    return module


def _run(tmp_path: Path, boot_sets_wal: bool) -> None:
    db = tmp_path / "platform.db"
    store = _store_module(tmp_path, db)

    def upgrade_head():
        if boot_sets_wal:
            conn = sqlite3.connect(str(db))
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            finally:
                conn.close()

    ns = {"store": store, "upgrade_head": upgrade_head, "Path": Path}
    exec(compile(_emitted_test_source(), "emitted", "exec"), ns)
    ns[_NAME](None)


def test_wal_set_by_the_boot_path_satisfies_the_emitted_check(tmp_path):
    """The live shape: fails on the pre-fix generator, passes now."""
    _run(tmp_path, boot_sets_wal=True)


def test_a_product_whose_boot_never_sets_wal_still_fails(tmp_path):
    try:
        _run(tmp_path, boot_sets_wal=False)
    except AssertionError as exc:
        assert "WAL is set by none of" in str(exc)
    else:
        raise AssertionError("WAL was not required")


def test_the_writer_is_told_the_same_contract():
    from app.factory.build import writer_prompt

    src = Path(writer_prompt.__file__).read_text(encoding="utf-8")
    assert "connect() sets ``PRAGMA journal_mode=WAL``" not in src
    assert "app.migrations.upgrade_head() switches" in src
