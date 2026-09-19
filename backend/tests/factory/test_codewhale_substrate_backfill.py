"""The CodeWhale writer path must satisfy the contract TESTER stamps.

``run_writer`` returns at its CodeWhale branch, and
``FACTORY_CODEWHALE_WRITER=1`` makes that the only path production takes --
so ``emit_writer_artifacts``, further down that function, never runs. But
``run_tester`` still stamps ``tests/test_data_lifecycle.py``, whose first
line is ``from app import backup, store``. Live build
sess_b6d51f9089e14176 went red on exactly that:

    ImportError: cannot import name 'backup' from 'app'

The agent is never asked for those files -- the writer prompt names
``app/migrations/`` and ``scripts/release_gate.py`` and says nothing about
backup, alembic, or an entrypoint -- so without a backfill it has to
reverse-engineer the substrate contract from red tests, one rework round
per missing file.
"""

from __future__ import annotations

from pathlib import Path

from app.factory.build.data_lifecycle import (
    REVISION_0002,
    backfill_platform_substrate,
    platform_substrate,
    render_product_tests,
)


class _Workspace:
    """Minimal RoleWorkspace stand-in: write_text + exists over one root."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.written: list[str] = []

    def write_text(self, relpath, content: str) -> Path:
        dest = self.root / Path(relpath)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        self.written.append(Path(relpath).as_posix())
        return dest

    def exists(self, relpath) -> bool:
        return (self.root / Path(relpath)).exists()


SPECS = {"booking": {"entity": "booking", "fields": [{"name": "reference", "type": "string"}]}}


def test_the_module_the_emitted_suite_imports_is_written(tmp_path):
    """The exact live failure: tests/test_data_lifecycle.py imports app.backup."""
    suite = render_product_tests(SPECS)
    assert "from app import backup, store" in suite, "test contract changed"

    ws = _Workspace(tmp_path)
    result = backfill_platform_substrate(ws)

    assert "app/backup.py" in result["written"]
    assert (tmp_path / "app" / "backup.py").is_file()
    assert (tmp_path / "app" / "migrations.py").is_file()


def test_every_substrate_file_lands_when_the_agent_wrote_none(tmp_path):
    ws = _Workspace(tmp_path)
    result = backfill_platform_substrate(ws)

    expected = [rel for rel, _ in platform_substrate()]
    assert sorted(result["written"]) == sorted(expected)
    assert result["skipped"] == []
    for rel in expected:
        assert (tmp_path / rel).is_file(), rel


def test_the_agents_own_files_are_never_overwritten(tmp_path):
    """Fills gaps; does not converge. The agent's bytes must survive."""
    ws = _Workspace(tmp_path)
    mine = "# authored by the coding agent, not the factory\n"
    (tmp_path / "app").mkdir(parents=True, exist_ok=True)
    (tmp_path / "app" / "backup.py").write_text(mine, encoding="utf-8")

    result = backfill_platform_substrate(ws)

    assert "app/backup.py" in result["skipped"]
    assert "app/backup.py" not in result["written"]
    assert (tmp_path / "app" / "backup.py").read_text(encoding="utf-8") == mine


def test_the_schema_bearing_files_stay_the_agents(tmp_path):
    """store.py and 0001_baseline carry the entity schema -- never backfilled."""
    rels = [rel for rel, _ in platform_substrate()]
    assert "app/store.py" not in rels
    assert "alembic/versions/0001_baseline.py" not in rels
    # The lifecycle-audit revision has no specs in it, so it is substrate.
    assert f"alembic/versions/{REVISION_0002}.py" in rels


def test_a_module_never_shadows_a_package_the_agent_wrote(tmp_path):
    """The writer prompt asks for app/migrations/; the suite needs
    app/migrations.py. With both present the package wins and the import
    fails in a way that reads as the agent's bug."""
    ws = _Workspace(tmp_path)
    pkg = tmp_path / "app" / "migrations"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("# agent's package\n", encoding="utf-8")

    result = backfill_platform_substrate(ws)

    assert not (tmp_path / "app" / "migrations.py").exists()
    assert any(s.startswith("app/migrations.py") for s in result["skipped"]), result
    # The rest of the substrate still lands.
    assert "app/backup.py" in result["written"]


def test_backfill_is_idempotent(tmp_path):
    ws = _Workspace(tmp_path)
    first = backfill_platform_substrate(ws)
    second = backfill_platform_substrate(ws)

    assert first["written"]
    assert second["written"] == []
    assert sorted(second["skipped"]) == sorted(first["written"])


def test_the_codewhale_path_calls_the_backfill():
    """The wiring, not just the helper: the production path must use it."""
    import inspect

    from app.factory.build import roles_handlers

    src = inspect.getsource(roles_handlers._run_writer_via_codewhale_worker)
    assert "backfill_platform_substrate" in src, (
        "the CodeWhale writer path does not backfill the substrate -- the "
        "helper exists but production never calls it"
    )


#: app/ modules the coding agent authors (schema-bearing or capability code).
#: Everything else the emitted suite imports must be factory substrate.
AGENT_OWNED = {"app/store.py"}


def test_every_app_module_the_emitted_suite_imports_has_an_owner():
    """The divergence guard.

    The writer prompt and the emitted suite drifted apart: the prompt names
    ``app/migrations/`` and ``scripts/release_gate.py`` and never mentions
    backup, alembic or an entrypoint, while the suite imports
    ``app.backup`` and ``app.migrations``. emit_writer_artifacts used to
    bridge that, and the CodeWhale early return took it out of production.
    Any future import added to the suite must land in the substrate or be
    explicitly declared agent-owned -- never silently unowned.
    """
    import re

    suite = render_product_tests(SPECS)
    modules: set[str] = set()
    for line in suite.splitlines():
        m = re.match(r"\s*from app import ([\w, ]+)", line)
        if m:
            modules.update(f"app/{n.strip()}.py" for n in m.group(1).split(","))
            continue
        m = re.match(r"\s*from app\.(\w+) import ", line)
        if m:
            modules.add(f"app/{m.group(1)}.py")

    assert modules, "no app imports parsed -- the parser or the suite changed"
    owned = {rel for rel, _ in platform_substrate()} | AGENT_OWNED
    unowned = sorted(modules - owned)
    assert not unowned, (
        "the emitted suite imports app modules nothing is required to write: "
        + ", ".join(unowned)
    )


STAMP = '"""Written by the factory WRITER role (codewhale exec)"""\n'


def _handler(root: Path, cap: str) -> None:
    actions = root / "app" / "actions"
    actions.mkdir(parents=True, exist_ok=True)
    (actions / f"{cap}.py").write_text(
        STAMP + f'CAPABILITY_ID = "{cap}"\n\n\ndef handle(payload):\n    return payload\n',
        encoding="utf-8",
    )


class TestReworkRoundIsNotJudgedEmpty:
    """A rework round that writes no NEW handler is not a silent writer.

    ``persist_workspace_root`` returns the staging root, and staging is
    rmtree'd fresh each phase, so the role's authored count saw only the
    current round. A round that fixed app/dispatch.py and a route scored
    authored=0 and killed a build carrying stamped handlers -- live
    sess_b6d51f9089e14176: status='completed' tools=123 authored=0.
    """

    def test_handlers_already_committed_are_counted(self, tmp_path):
        from app.factory.build.authorship import (
            agent_written_handler_ids_in_workspace,
        )

        destination = tmp_path / "build"
        staging = tmp_path / ".build.staging-writer"
        staging.mkdir(parents=True, exist_ok=True)
        _handler(destination, "record_checkin")
        _handler(destination, "list_todays_checkins")
        # This round fixed a non-handler file only.
        (staging / "app").mkdir(parents=True, exist_ok=True)
        (staging / "app" / "dispatch.py").write_text("# fixed\n", encoding="utf-8")

        assert agent_written_handler_ids_in_workspace(staging) == []
        carried = agent_written_handler_ids_in_workspace(destination)
        assert sorted(carried) == ["list_todays_checkins", "record_checkin"]

    def test_the_role_falls_back_to_the_committed_tree(self):
        """The wiring: the refusal must consult the destination."""
        import inspect

        from app.factory.build import roles_handlers

        src = inspect.getsource(roles_handlers._run_writer_via_codewhale_worker)
        assert "carried_over" in src
        assert "not (authored or carried_over)" in src, (
            "writer_no_output still judges the staging round alone -- a rework "
            "round that writes no new handler will kill the build"
        )

    def test_a_writer_that_produced_nothing_at_all_is_still_refused(self, tmp_path):
        """The check must not become vacuous: zero everywhere is still zero."""
        from app.factory.build.authorship import (
            agent_written_handler_ids_in_workspace,
        )

        destination = tmp_path / "build"
        destination.mkdir(parents=True, exist_ok=True)
        staging = tmp_path / ".build.staging-writer"
        staging.mkdir(parents=True, exist_ok=True)

        assert agent_written_handler_ids_in_workspace(staging) == []
        assert agent_written_handler_ids_in_workspace(destination) == []


class TestWriterPromptNamesTheRealContract:
    """The prompt, the substrate and the emitted suite are one contract.

    v2 of the prompt asked for an ``app/migrations/`` package and never
    mentioned store/backup/alembic, so the agent learned the persistence
    contract from red tests. These bind the three together.
    """

    def _prompt(self) -> str:
        from app.factory.build.writer_prompt import render_writer_prompt

        class _Bp:
            product_id = "probe"
            product_name = "Probe"
            vertical = "probe"
            summary = "probe"

        return render_writer_prompt(_Bp(), brief="x")

    def test_the_prompt_no_longer_asks_for_the_shadowing_package(self):
        prompt = self._prompt()
        assert "- app/block_inputs.py, app/migrations/" not in prompt
        assert "Do not\ncreate an app/migrations/ package" in prompt

    def test_every_python_substrate_module_is_named_as_factory_written(self):
        prompt = " ".join(self._prompt().split())
        for rel, _ in platform_substrate():
            if rel.endswith(".py") or rel.endswith(".sh") or rel == "alembic.ini":
                assert rel in prompt, f"{rel} is backfilled but the prompt never says so"

    def test_the_agent_owned_files_are_named_with_their_surface(self):
        prompt = " ".join(self._prompt().split())
        for rel in AGENT_OWNED | {"alembic/versions/0001_baseline.py"}:
            assert rel in prompt, rel
        # Every store attribute the emitted suite touches is in the prompt.
        import re

        suite = render_product_tests(SPECS)
        used = set(re.findall(r"\bstore\.([A-Za-z_]+)", suite))
        missing = sorted(a for a in used if a not in prompt)
        assert not missing, f"the suite calls store.{missing} but the prompt never asks for it"
