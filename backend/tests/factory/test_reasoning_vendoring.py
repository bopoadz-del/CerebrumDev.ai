"""Vendoring the kit into the product — the step that makes a built platform gate.

The socket used to emit five code files and leave ``app/reasoning/kit/`` empty. The
kernel then disabled itself and refused every statement, which is correct by the
fail-closed rule and useless: the platform gated nothing and could answer nothing
either. These tests drive the vendoring, and the last one takes a REAL Store kit
through it and runs the emitted kernel against it, because "the files were copied"
is not the same claim as "the built platform gates".
"""
from __future__ import annotations

import importlib
import pathlib
import sys
import types

import pytest
import yaml

from app.factory.build import reasoning_socket


class FakeWorkspace:
    """Records what the build wrote, and writes it, so a test can import it."""

    def __init__(self, root: pathlib.Path) -> None:
        self.root = root
        self.written: list = []

    def write_text(self, relative, body: str) -> None:
        target = self.root / pathlib.Path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        self.written.append(str(pathlib.Path(relative)).replace("\\", "/"))


class FakeCtx:
    def __init__(self, root: pathlib.Path, vertical=None) -> None:
        self.workspace = FakeWorkspace(root)
        self.blueprint = types.SimpleNamespace(vertical=vertical)
        self.plan = None


def _store(tmp_path: pathlib.Path, kit: str, *, manifest=None, invariants=None,
           questions=None) -> pathlib.Path:
    """A minimal Store tree holding one kit."""
    blocks = tmp_path / "store" / "app" / "blocks" / kit
    blocks.mkdir(parents=True, exist_ok=True)
    (blocks / "manifest.yaml").write_text(
        yaml.safe_dump(manifest if manifest is not None else {
            "kit": kit, "version": 1,
            "quantities": {"rate": {"units": ["currency_per_m2"]}},
            "figures": {"rate": {"value": None, "question": "derived: what is the rate?"}},
        }), encoding="utf-8")
    (blocks / "invariants.yaml").write_text(
        yaml.safe_dump(invariants if invariants is not None else {
            "invariants": [{
                "id": "INV-1", "kind": "grounding", "severity": "refuse", "hook": "H3",
                "applies_to": {"quantity": "any"}, "message": "{quantity} is not grounded",
                "measurement": "20 probes. Before: n uncited. After: 0.",
            }]
        }), encoding="utf-8")
    if questions is not None:
        (blocks / "questions.yaml").write_text(
            yaml.safe_dump(questions, allow_unicode=True), encoding="utf-8")
    return tmp_path / "store"


@pytest.fixture
def store_root(monkeypatch):
    """Point the Store resolver wherever a test says."""
    holder = {}

    def resolve():
        return holder.get("root")

    module = importlib.import_module("app.factory.blocks_source")
    monkeypatch.setattr(module, "resolve_blocks_root", resolve)
    return holder


# ── the three stated outcomes ─────────────────────────────────────────────

def test_a_vertical_with_no_kit_vendors_nothing_and_the_platform_still_builds(
        tmp_path, store_root, caplog):
    store_root["root"] = _store(tmp_path, "fitout")
    ctx = FakeCtx(tmp_path / "product", vertical=None)
    with caplog.at_level("WARNING"):
        written = reasoning_socket.emit(ctx)
    assert not [p for p in written if "/kit/" in p]
    assert reasoning_socket.KERNEL_PATH in written, "the socket still ships"
    assert "refuse every figure" in caplog.text


def test_an_unreachable_store_is_an_error_not_a_shrug(tmp_path, store_root, caplog):
    store_root["root"] = None
    ctx = FakeCtx(tmp_path / "product", vertical="fitout")
    with caplog.at_level("ERROR"):
        written = reasoning_socket.emit(ctx)
    assert not [p for p in written if "/kit/" in p]
    assert "Store unreachable" in caplog.text
    assert "refuse every figure" in caplog.text


def test_a_kit_with_no_invariants_fails_the_build_rather_than_shipping_a_dead_gate(
        tmp_path, store_root):
    store_root["root"] = _store(tmp_path, "fitout", invariants={"invariants": []})
    ctx = FakeCtx(tmp_path / "product", vertical="fitout")
    with pytest.raises(ValueError) as exc:
        reasoning_socket.emit(ctx)
    assert "no invariants" in str(exc.value)


def test_a_kit_whose_manifest_names_another_kit_fails_the_build(tmp_path, store_root):
    """Vendoring it would give this platform one domain's vocabulary under
    another domain's name."""
    store_root["root"] = _store(tmp_path, "fitout", manifest={
        "kit": "rail", "version": 1,
        "quantities": {"rate": {"units": ["currency_per_m2"]}},
    })
    ctx = FakeCtx(tmp_path / "product", vertical="fitout")
    with pytest.raises(ValueError) as exc:
        reasoning_socket.emit(ctx)
    assert "rail" in str(exc.value) and "fitout" in str(exc.value)


def test_a_kit_missing_its_manifest_fails_the_build(tmp_path, store_root):
    root = _store(tmp_path, "fitout")
    (root / "app" / "blocks" / "fitout" / "manifest.yaml").unlink()
    store_root["root"] = root
    ctx = FakeCtx(tmp_path / "product", vertical="fitout")
    with pytest.raises(FileNotFoundError):
        reasoning_socket.emit(ctx)


# ── what actually gets vendored ───────────────────────────────────────────

def test_the_kit_and_the_owners_sheet_are_both_vendored(tmp_path, store_root):
    sheet = {
        "kit": "fitout", "title": "Fit-Out — Questions I Cannot Answer",
        "answer_format": ["value", "unit", "quality band", "market", "source", "date"],
        "sections": {"B": {"title": "Rates"}},
        "questions": [{"id": "B.1", "section": "B", "gate": True, "covers": ["rate"],
                       "text": "Cost per m² all-in by your quality band."}],
    }
    store_root["root"] = _store(tmp_path, "fitout", questions=sheet)
    ctx = FakeCtx(tmp_path / "product", vertical="fitout")
    written = reasoning_socket.emit(ctx)
    assert reasoning_socket.KIT_MANIFEST in written
    assert reasoning_socket.KIT_INVARIANTS in written
    assert reasoning_socket.KIT_QUESTIONS in written
    landed = yaml.safe_load(
        (tmp_path / "product" / reasoning_socket.KIT_QUESTIONS).read_text(encoding="utf-8"))
    assert landed["questions"][0]["text"] == sheet["questions"][0]["text"], (
        "the owner's wording must arrive byte-for-byte, not reformatted in transit"
    )


def test_a_kit_without_a_sheet_vendors_and_warns_rather_than_failing(
        tmp_path, store_root, caplog):
    """Two Store kits have no sheet. They must still produce a working platform,
    and the log must not let that pass as an interview that has been done."""
    store_root["root"] = _store(tmp_path, "datacentre")
    ctx = FakeCtx(tmp_path / "product", vertical="datacentre")
    with caplog.at_level("WARNING"):
        written = reasoning_socket.emit(ctx)
    assert reasoning_socket.KIT_MANIFEST in written
    assert reasoning_socket.KIT_QUESTIONS not in written
    assert "NO owner question sheet" in caplog.text
    assert "derived" in caplog.text


# ── end to end: a real Store kit, driven through the emitted kernel ───────

def test_a_real_store_kit_vendors_and_the_emitted_kernel_gates_with_it(
        tmp_path, store_root, monkeypatch):
    """The claim this file exists to support: not that files were copied, but that
    the built platform gates, and asks the owner's questions while doing it."""
    store = pathlib.Path("C:/Users/shimm/Cerebrum-Blocks")
    if not (store / "app" / "blocks" / "fitout" / "questions.yaml").is_file():
        pytest.skip("the Store checkout is not beside this worktree")
    store_root["root"] = store

    product = tmp_path / "product"
    ctx = FakeCtx(product, vertical="fitout")
    written = reasoning_socket.emit(ctx)
    assert reasoning_socket.KIT_QUESTIONS in written

    # Import it the way a product does: emit writes to app/reasoning/*, so the
    # product's own `app` package is what has to be mounted under that name.
    inner = product / "app"
    (inner / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.syspath_prepend(str(product))
    for name in [n for n in list(sys.modules)
                 if n in ("app", "builtapp") or n.startswith("app.")
                 or n.startswith("builtapp.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    kernel_mod = importlib.import_module("app.reasoning.kernel")

    kernel = kernel_mod.kernel
    assert kernel.enabled, f"the vendored kit did not load: {kernel.disabled_reason}"

    # It gates: an ungrounded figure is refused by the real fit-out invariants.
    outcome = kernel.answer_time([kernel_mod.Figure(quantity="rate", value=2500, unit="currency_per_m2")])
    assert outcome.verdict == "refused"

    # And it asks the owner's own questions, marked as the owner marked them.
    state = kernel.interview()
    assert state["questions_source"] == "owner_sheet"
    assert state["questions"] == 62 and state["gating"] == 21
    assert state["ready"] is False
    assert state["required_fields"] == [
        "unit", "quality_band", "market", "source", "date", "confirmed_or_indicative"]
    first = state["next"][0]
    assert first["id"] == "B.1" and first["marked"] == "GATE"
    assert "quality band" in first["text"]

    # The rate figure's question is the owner's wording, not the derived one — and
    # it is B.2 ("your rate per package"), not B.1 ("cost per m² by quality band"),
    # because the link is an exact naming of the quantity rather than a guess at
    # which question sounds relevant.
    pending = importlib.import_module("app.reasoning.pending")
    asked = pending.unanswered()["rate"]
    assert "B.2" in asked and "rate per package" in asked
    assert "what is the rate" not in asked.lower(), (
        "the derived question won over the owner's own"
    )

    # An answer needs every field this domain requires, and then it sticks.
    with pytest.raises(ValueError):
        kernel.record_question_answer("B.1", "2500/m2", {"unit": "currency_per_m2"})
    kernel.record_question_answer("B.1", "2500/m2", {
        "unit": "currency_per_m2", "quality_band": "standard", "market": "Dubai",
        "source": "our 2026 tender book", "date": "2026-09-24",
        "confirmed_or_indicative": "confirmed"})
    assert kernel.interview()["outstanding"] == 20

    # And it never landed in the kit.
    kit_dir = inner / "reasoning" / "kit"
    assert sorted(p.name for p in kit_dir.iterdir()) == [
        "invariants.yaml", "manifest.yaml", "questions.yaml"]
