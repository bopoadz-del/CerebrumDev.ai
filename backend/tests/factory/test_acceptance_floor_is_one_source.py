"""One spec, two consumers — and a test that fails when they drift.

The Store gate graded every build against thirteen checks the coder was never
told. Measured, not assumed: before this landed, grepping the writer prompt
and the C-BRIEF for ``no_token_401`` returned zero, and the same for every
other check id. So the agent discovered the floor by failing it, and each
discovery cost a full writer pass.

Putting the rules in the brief alone would move the trust back to the author,
which is what the gate exists to remove. So the floor lives in one file and
both sides read it: the prompt renders ``requirement``, the gate takes its
checklist from the ids. This module is the part that keeps that true — if
either consumer grows its own copy, this goes red.
"""

from __future__ import annotations

import pathlib

from app.factory.build.acceptance_floor import (
    check_ids,
    checks,
    floor_hash,
    floor_path,
    floor_version,
    gate_fns,
    render_for_prompt,
    requirements,
)
from app.factory.build.store_acceptance import (
    ACCEPTANCE_CHECK_NAMES,
    ACCEPTANCE_REQUIRED,
)
from app.factory.build.writer_prompt import render_writer_prompt


class _Blueprint:
    product_id = "bakery-operations"
    product_name = "Bakery Branch Operations Platform"
    vertical = "bakery"
    summary = "branch ops"


def _prompt() -> str:
    return render_writer_prompt(_Blueprint(), brief="a bakery")


class TestTheGateReadsTheFloor:
    def test_the_checklist_is_not_a_second_copy(self):
        assert ACCEPTANCE_CHECK_NAMES == check_ids()

    def test_the_required_count_matches_what_the_floor_actually_holds(self):
        assert ACCEPTANCE_REQUIRED == len(check_ids())

    def test_authorship_floor_stays_last(self):
        """The gate's own assertion, restated where the order is decided."""
        assert check_ids()[-1] == "authorship_floor"


class TestTheCoderIsToldTheFloor:
    def test_every_check_the_gate_grades_appears_in_the_prompt(self):
        """The regression this file exists for.

        Before the floor was one file, this assertion failed on all 13.
        """
        text = _prompt()
        missing = [cid for cid in check_ids() if cid not in text]
        assert not missing, f"graded on but never told: {missing}"

    def test_the_requirement_text_is_rendered_not_paraphrased(self):
        text = _prompt()
        missing = [r for r in requirements() if r not in text]
        assert not missing, f"requirement reworded between spec and prompt: {missing}"

    def test_the_prompt_names_the_floor_version(self):
        """A prompt and a gate on different versions is the drift itself."""
        assert f"v{floor_version()}" in render_for_prompt()
        assert f"v{floor_version()}" in _prompt()

    def test_the_floor_is_stated_as_a_bar_not_as_advice(self):
        assert "not advice" in _prompt()


class TestTheSpecIsWellFormed:
    def test_every_check_says_both_what_to_build_and_what_is_verified(self):
        for check in checks():
            assert check["requirement_text"].strip(), check["id"]
            assert check["check"].strip(), check["id"]

    def test_requirements_are_written_for_the_builder_not_the_grader(self):
        """A requirement that only restates the check teaches the coder
        nothing it could not have guessed from the failure message."""
        for check in checks():
            assert check["requirement_text"].strip() != check["check"].strip(), check["id"]

    def test_ids_are_unique(self):
        assert len(set(check_ids())) == len(check_ids())


# --- P3: the drift lock ------------------------------------------------------


class TestNeitherConsumerCanDriftFromTheFile:
    def test_both_renders_are_pinned_to_the_same_floor_bytes(self):
        """A change that reaches one consumer and not the other is red.

        Both renders are derived here from the same hash, so this cannot be
        satisfied by updating one side.
        """
        digest = floor_hash()
        assert digest.startswith("sha256:")
        brief = render_for_prompt()
        from app.factory.build.store_acceptance import render_acceptance_script

        gate = render_acceptance_script()
        for cid in check_ids():
            assert cid in brief, f"{cid} missing from the brief render"
            assert cid in gate, f"{cid} missing from the gate render"
        assert floor_hash() == digest, "the floor changed mid-test"

    def test_the_harness_defines_every_gate_fn_the_floor_names(self):
        from app.factory.build.store_acceptance import render_acceptance_script

        gate = render_acceptance_script()
        missing = [fn for fn in gate_fns() if f"def {fn}(" not in gate]
        assert not missing, f"floor names gate fns the harness does not define: {missing}"

    def test_no_hand_kept_check_id_list_outside_the_floor(self):
        """The grep gate, as a test.

        A second roster is the thing the floor file abolishes: it drifts the
        moment a check is added, and the build is then graded against a list
        nobody updated. Defining check_<id> is not enumerating -- the
        implementations have to live somewhere. Quoting five or more ids as
        string literals in one file is.
        """
        import pathlib
        import re

        root = pathlib.Path(__file__).resolve().parents[2] / "app"
        ids = set(check_ids())
        offenders = []
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            quoted = {
                cid
                for cid in ids
                if re.search(r"[\"']" + re.escape(cid) + r"[\"']", text)
            }
            if len(quoted) >= 5:
                offenders.append(f"{path.relative_to(root)} ({len(quoted)} ids)")
        assert offenders == [], (
            "check ids enumerated outside acceptance_floor.v2.json: "
            + ", ".join(offenders)
        )

    def test_the_floor_file_is_where_the_ids_live(self):
        text = floor_path().read_text(encoding="utf-8")
        for cid in check_ids():
            assert cid in text


class TestTheFloorIsProductAgnostic:
    """The floor grades every product the Factory makes, so it may not
    describe any one of them.

    ``negative_floor`` shipped asking for the boundary case "closed exactly
    at due_at". ``due_at`` is a column in one facility-management build,
    carried in verbatim from an audit of that export -- so every other
    product would have been handed a rule naming a field it does not have.

    The first guard for this was a list of domain nouns I typed out, which
    is the same mistake one level up: a hand-kept list that goes stale the
    day a new vertical ships. Both halves are derived instead. A DOMAIN is
    whatever the Factory already calls one -- the ``vertical`` of its own
    blueprints, and the Store's ``<domain>_v2`` registry entries. A FIELD is
    caught by shape, not by name, so a column nobody has seen yet is caught
    on the first build that mentions it.
    """

    @staticmethod
    def _factory_domains() -> set:
        """Domains as WHOLE names, never split into words.

        Splitting on "_" turned real_estate into "real", which then matched
        "a real outbound delivery" and "real DDL" -- a guard that fires on
        ordinary English is a guard that gets deleted. A domain is the whole
        name, in both the underscore and spaced spellings.
        """
        import re as _re

        here = pathlib.Path(__file__).resolve()
        repo = here.parents[3]
        names = set()
        for yaml_path in (repo / "blueprints").rglob("*.yaml"):
            for line in yaml_path.read_text(
                encoding="utf-8", errors="ignore"
            ).splitlines():
                m = _re.match(r"\s*vertical:\s*['\"]?([a-z_]+)", line)
                if m:
                    names.add(m.group(1))
        try:
            from app.factory.dual_registry import _default_blocks_root

            registry = _default_blocks_root() / "block_registry"
            for entry in registry.iterdir() if registry.is_dir() else []:
                if entry.is_dir() and entry.name.endswith("_v2"):
                    names.add(entry.name[: -len("_v2")])
        except Exception:
            pass
        names -= {"product", "field_operations"}  # too generic to mean a domain
        out = set()
        for name in names:
            if len(name) > 3:
                out.add(name)
                out.add(name.replace("_", " "))
        return out

    #: A domain column, by shape: the suffixes a generated entity uses.
    FIELD_SHAPE = r"(?<![a-z0-9_])[a-z]+_(?:at|id|no|ref|code|date|url)(?![a-z0-9_])"

    #: Platform vocabulary the floor is entitled to name.
    PLATFORM_WORDS = frozenset(
        {"database_url", "redis_url", "storage_path", "blocks_unavailable"}
    )

    def _floor_text(self) -> str:
        return " ".join(
            f"{c['requirement_text']} {c['brief_render']} {c['check']}"
            for c in checks()
        ).lower()

    def test_no_domain_the_factory_knows_appears_in_the_floor(self):
        domains = self._factory_domains()
        assert domains, "could not derive the Factory's domains; the guard is blind"
        text = self._floor_text()
        found = sorted(w for w in domains if w in text)
        assert not found, (
            "the floor names a domain the Factory builds for: "
            + ", ".join(found)
            + " -- every product is graded against this file"
        )

    def test_no_domain_column_shape_appears_in_the_floor(self):
        """Catches due_at, invoice_no, tenant_ref without knowing them."""
        import re as _re

        found = {
            m.group(0)
            for m in _re.finditer(self.FIELD_SHAPE, self._floor_text())
            if m.group(0) not in self.PLATFORM_WORDS
        }
        assert not found, f"the floor names a product's columns: {sorted(found)}"

    def test_the_floor_reaches_the_prompt_without_naming_a_product(self):
        import re as _re

        text = render_for_prompt().lower()
        domains = sorted(w for w in self._factory_domains() if w in text)
        columns = sorted(
            {
                m.group(0)
                for m in _re.finditer(self.FIELD_SHAPE, text)
                if m.group(0) not in self.PLATFORM_WORDS
            }
        )
        assert not domains and not columns, (
            f"product vocabulary reaches the coder's prompt: {domains} {columns}"
        )
