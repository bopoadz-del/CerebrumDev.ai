"""NO HARDWIRING -- the rule's one home in CI.

Owner's rule (2026-10-02): a failing case is never fixed by name. No per-case
branches, no literal document strings, no product/capability/session names, no
word lists, no rescue functions, no per-case switches. Fix the mechanism so
every case of that kind passes.

Each test below locks one mechanism that replaced a by-name fix, and each runs
on invented names the Factory has never seen:

  * a brief carries a kit's contract, never its provenance or another
    product's capabilities (kit-manifest leak);
  * a RAG surface is a whole token of the capability id ("storage" is not RAG);
  * the hardwiring gate refuses a word list -- a literal collection searched
    inside text -- and nothing that is a closed vocabulary or a key lookup;
  * the Store's stub audit treats typing.Protocol members as interface
    declarations, like abstractmethods, and still flags the same body
    anywhere else.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.brief_compiler import compile_brief
from app.factory.build.brief_lint import lint_brief
from app.factory.build.writer_phases import inventory_needs_rag
from app.factory.product_architect import plan_blueprint

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"
GATE = ROOT / "scripts" / "scan_hardwiring.py"


def _compiled(path=SMOKE):
    bp = load_blueprint(path)
    return compile_brief(bp, plan_blueprint(bp))


def _gate():
    spec = importlib.util.spec_from_file_location("scan_hardwiring", GATE)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


# --- kit-manifest leak ---------------------------------------------------------


def test_kit_manifest_in_a_brief_is_identity_plus_claimed_contract_only():
    compiled = _compiled()
    kit = next(iter(compiled.kit_manifests.values()))
    assert not {"capabilities", "blueprint", "author", "artifacts"} & set(kit)
    assert lint_brief(compiled).ok, lint_brief(compiled).errors


def test_a_kit_provenance_key_planted_in_a_brief_is_refused():
    compiled = _compiled()
    kit_id = next(iter(compiled.kit_manifests))
    compiled.kit_manifests[kit_id] = dict(
        compiled.kit_manifests[kit_id], capabilities=["zorblat_intake"])
    assert any("non-contract keys: capabilities" in e for e in lint_brief(compiled).errors)


def test_a_kit_contract_naming_an_unclaimed_block_is_refused():
    compiled = _compiled()
    kit_id = next(iter(compiled.kit_manifests))
    kit = dict(compiled.kit_manifests[kit_id])
    kit["blocks"] = {"group": list(kit.get("product_blocks") or []) + ["zorblat_block"]}
    compiled.kit_manifests[kit_id] = kit
    assert any("did not claim" in e for e in lint_brief(compiled).errors)


# --- whole-token RAG ------------------------------------------------------------


class _Cap:
    def __init__(self, cid, block_ids):
        self.capability_id = cid
        self.block_ids = list(block_ids)
        self.strategy = "REUSE"
        self.notes = cid


class _Plan:
    def __init__(self, *caps):
        self.capabilities = caps


class _Blueprint:
    product_name = "Zorblat Yard"
    product_id = "zorblat-yard"
    vertical = "zorblat_yards"
    summary = "Track zorblats."


def _store_root(tmp_path, reads_by_block):
    """An invented Store: each block's block.json declares the given reads."""
    import json

    root = tmp_path / "store"
    for bid, reads in reads_by_block.items():
        d = root / "block_registry" / bid
        d.mkdir(parents=True)
        (d / "block.json").write_text(json.dumps({"id": bid, "reads": reads}), encoding="utf-8")
    return root


@pytest.mark.parametrize("cap_id, block_id, owes_rag", [
    ("zorblat_rag_answers", "zorblat_ledger", False),  # "rag" in the name decides nothing
    ("storage_management", "zorblat_index", True),     # the bound block retrieves
    ("rag", "zorblat_ledger", False),
])
def test_a_rag_surface_follows_what_the_bound_block_declares(tmp_path, monkeypatch, cap_id, block_id, owes_rag):
    root = _store_root(tmp_path, {
        "zorblat_index": [{"kind": "database", "scope": "vector"}],
        "zorblat_ledger": [{"kind": "database", "scope": "sql"}],
    })
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(root))
    compiled = compile_brief(_Blueprint(), _Plan(_Cap(cap_id, [block_id])), store_ids={block_id})
    assert inventory_needs_rag(compiled) is owes_rag

# --- the word-list form ---------------------------------------------------------


def test_a_two_word_list_searched_in_text_is_refused():
    found = _gate().word_lists(
        'WORDS = ("zorblat yard", "quillon")\n'
        "def route(brief):\n"
        "    return any(w in brief.lower() for w in WORDS)\n")
    assert found and found[0][1] == "WORDS"


def test_closed_vocabularies_and_key_lookups_are_not_word_lists():
    assert _gate().word_lists(
        "def f(mode, d, done):\n"
        "    if mode in ('zip', 'github_repo'):\n"
        "        pass\n"
        "    for k in ('alpha', 'beta'):\n"
        "        if k in d:\n"
        "            d[k] = 1\n"
        "    return all(r in done for r in ('A', 'B'))\n") == []


def test_members_loaded_from_data_are_not_literals():
    assert _gate().word_lists(
        "import json\n"
        "WORDS = tuple(json.load(open('store.json')))\n"
        "def f(t: str):\n    return any(w in t for w in WORDS)\n") == []


# --- Protocol members in the Store's stub audit ----------------------------------


def _store_audit() -> Path:
    root = os.environ.get("CEREBRUM_BLOCKS_ROOT")
    script = Path(root) / "scripts" / "audit_stubs.py" if root else None
    if script is None or not script.is_file():
        pytest.fail("CEREBRUM_BLOCKS_ROOT must name a Store checkout (CI sets it)")
    return script


def test_protocol_members_are_interfaces_and_the_same_body_elsewhere_is_a_stub(tmp_path):
    script = _store_audit()
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "iface.py").write_text(
        "from typing import Protocol\n"
        "class ZorblatStore(Protocol):\n"
        "    def read(self, key: str) -> str: ...\n", encoding="utf-8")
    ok = subprocess.run([sys.executable, str(script)], cwd=tmp_path,
                        capture_output=True, text=True, check=False)
    assert ok.returncode == 0, ok.stdout
    (tmp_path / "pkg" / "impl.py").write_text(
        "class ZorblatImpl:\n    def read(self, key: str) -> str: ...\n", encoding="utf-8")
    bad = subprocess.run([sys.executable, str(script)], cwd=tmp_path,
                         capture_output=True, text=True, check=False)
    assert bad.returncode == 1 and "pkg/impl.py" in bad.stdout and "iface.py" not in bad.stdout


# --- the phrase-match form --------------------------------------------------------


@pytest.mark.parametrize("src", [
    'def f(message: str):\n    return "zorblat refused" in message\n',
    'def f(out: str):\n    return out.lower().startswith("zorblat")\n',
    'import re\ndef f(log: str):\n    return re.search("zorblat failed", log)\n',
    'def f(detail: str):\n    return detail == "zorblat went wrong"\n',
    'def f(e):\n    return "zorblat" in str(e)\n',
    # A phrase check inside a code template the Factory emits is still code.
    'TEMPLATE = """\nimport sys\n\ndef check(text: str):\n    return "zorblat" in text\n\n\nprint(1)\n"""\n',
])
def test_a_single_phrase_used_as_a_decision_on_text_is_caught(src):
    assert _gate().phrase_matches(src), src


@pytest.mark.parametrize("src", [
    '"""A docstring may say zorblat failed."""\n',
    '# a comment may say "zorblat" in message\nx = 1\n',
    'def f(p: str):\n    return p.endswith(".py") or "/" in p\n',
    'def f(rec: dict):\n    return rec.get("zorblat")\n',
    'def f(kind: str):\n    return kind == "zorblat"\n',  # one token: closed vocabulary
])
def test_prose_paths_keys_and_closed_vocabulary_are_not_phrases(src):
    assert _gate().phrase_matches(src) == [], src
