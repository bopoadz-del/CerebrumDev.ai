"""The pre-writer stamp of the data-lifecycle suite carries the product's real
entity (release cycle 9 smoke B, loose end).

Live: smoke B (sess_e8aa8ad93a3a4d91, build/plt_f9c91b85bce748c5): the writer's
rework pass found tests/test_data_lifecycle.py stamped with ``ENTITY = ''``, so
test_parallel_writes_match_fastapi_threadpool was SKIPPED in every run the
writer made. TESTER then stamped it with the real entity and it failed on the
writer's store (QueuePool exhaustion) -- a failure the writer could not have
seen in its own pass. The pre-writer stamp ran TESTER against the STAGING
checkout (empty, no app/models.py) instead of the product tree the pass is
merged into, where the declared models live. It now reads the same declared
specs TESTER reads: the product tree's app/models.py.

Synthetic product only.
"""

from __future__ import annotations

import ast
from pathlib import Path

MODELS = '''
class UnitLog:
    FIELDS = ["reference", "status", "note"]
    CONSTRAINTS = {"status": {"allowed_values": ["open", "closed"]}}

    def __init__(self, **kw):
        self.__dict__.update(kw)

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: d.get(k) for k in cls.FIELDS})

    def to_dict(self):
        return {k: getattr(self, k, None) for k in self.FIELDS}


MODELS = {"unit_log": UnitLog}
'''

SUITE = "tests/test_data_lifecycle.py"


def _module_value(text: str, name: str):
    for node in ast.parse(text).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not assigned")


def test_the_pre_writer_lifecycle_suite_names_the_declared_entity(tmp_path):
    from app.factory.build import factory_owned
    from app.factory.build.authority import BuildRole
    from app.factory.build.roles import RoleContext
    from app.factory.build.workspace import RoleWorkspace

    product = tmp_path / "build"
    (product / "app").mkdir(parents=True)
    (product / "app" / "__init__.py").write_text("", encoding="utf-8")
    (product / "app" / "models.py").write_text(MODELS, encoding="utf-8")
    staging = tmp_path / ".build.staging-writer"
    staging.mkdir()
    ctx = RoleContext(
        role=BuildRole.WRITER,
        workspace=RoleWorkspace(BuildRole.WRITER, product, staging=staging),
        blueprint=None,
        plan=None,
        state={},
    )

    factory_owned.prestamp_absent_owned(staging, ctx)

    text = (staging / SUITE).read_text(encoding="utf-8")
    entity = _module_value(text, "ENTITY")
    assert entity == "unit_log", entity
    # The parallel-write test is gated on ENTITY: with it set, it runs.
    assert _module_value(text, "SAMPLE")
    assert Path(staging / SUITE).is_file()
