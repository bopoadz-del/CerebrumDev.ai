"""WAL must be switched once at boot, not on every connection.

The emitted connect() ran ``PRAGMA journal_mode=WAL`` on each connection. When
FastAPI's threadpool opens N connections at once on a freshly migrated file,
the mode switches race for the write lock and raise
``sqlite3.OperationalError: database is locked`` -- the failure
test_parallel_writes_match_fastapi_threadpool hit on Cerebrum VenueOps.

Measured (40 threads x 15 rounds): WAL-switched per connection -> 20-34 lock
errors; WAL set once at creation and connections left alone -> 0. WAL is a
persistent property of the file, so setting it once at migrate is all that is
needed; connect() then only sets the per-connection pragmas.
"""

from __future__ import annotations

from app.factory.build.data_lifecycle import render_migrations, render_store

_SPECS = {"book": {"entity": "book", "fields": [{"name": "title", "type": "str"}]}}


def test_connect_does_not_switch_journal_mode_per_connection():
    src = render_store(_SPECS)
    connect_body = src[src.index("def connect()"):src.index("def enable_wal(")]
    assert "journal_mode=WAL" not in connect_body, (
        "connect() switches journal mode on every connection; that is the race"
    )
    assert "busy_timeout" in connect_body, "per-connection busy_timeout must stay"


def test_store_enables_wal_once_out_of_band():
    src = render_store(_SPECS)
    assert "def enable_wal" in src, "a one-time WAL switch must exist"
    assert "journal_mode=WAL" in src, "enable_wal must set WAL"


def test_upgrade_head_sets_wal_at_boot():
    mig = render_migrations()
    head = mig[mig.index("def upgrade_head"):mig.index("def upgrade_to")]
    assert "journal_mode=WAL" in head, (
        "upgrade_head runs once before the threadpool serves; it must set WAL "
        "there so no connection has to switch it"
    )
    # Inline, not via app.store: store.py is writer-authored and may not expose
    # a WAL helper (the emitted-migrations test uses a floor-compliant store
    # that does not), so importing one there raises ImportError at boot.
    assert "from app.store import enable_wal" not in head
