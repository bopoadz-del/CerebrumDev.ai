"""The factory's output must DO work, and its provenance must be visible.

New-shape tests for the 2026-08-02 field findings (live hotel/retail runs):

1. Generated exports self-described but exposed NO route to their action
   modules — "a skeleton". Every generated main.py must wire
   ``POST /v1/actions/{capability_id}`` to the real handlers, and the
   export's own smoke suite must prove it.
2. The architect silently fell back from LLM to keyword templates when
   credit died — a paying user could not tell. Drafting mode must travel on
   the blueprint and reach the chat summary.
3. GENERATE capabilities shipped as stubs with no way to ever be real. The
   coder writes them via the factory LLM; on coder failure the stub ships
   HONESTLY (reason recorded in the generation result and chat summary).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("ALLOW_ANONYMOUS_DEV", "1")

from app.factory import coder as coder_mod  # noqa: E402
from app.factory import platform_chat_flow  # noqa: E402
from app.factory.coder import CoderError, _validate_body  # noqa: E402
from app.factory.product_architect import (  # noqa: E402
    draft_blueprint_from_brief,
    generate_product,
)
from app.models.session import ProductDesignState, SessionState  # noqa: E402

BRIEF = (
    "Build me a hotel management platform with dashboard analytics, "
    "database records, team coordination and audit trails."
)


def _draft(monkeypatch):
    monkeypatch.setenv(coder_mod.CODER_ENABLED_ENV, "0")
    return draft_blueprint_from_brief(BRIEF, use_llm=False)


# --- 1. Wiring: generated exports must be callable ---------------------------


class TestGeneratedActionsAreWired:
    def test_generated_main_exposes_action_routes(self, tmp_path, monkeypatch):
        bp = _draft(monkeypatch)
        out = tmp_path / "export"
        generate_product(bp, out)

        main_py = (out / "app" / "main.py").read_text(encoding="utf-8")
        assert '@app.post("/v1/actions/{capability_id}")' in main_py, (
            "generated main.py has no action route — the export is a skeleton"
        )
        assert '"dependency_required": 424' in main_py, (
            "outcome statuses must map to HTTP codes"
        )
        smoke = (out / "tests" / "test_smoke.py").read_text(encoding="utf-8")
        assert "test_actions_are_reachable_over_http" in smoke, (
            "the export's own smoke suite no longer proves actions are callable"
        )

    def test_generated_export_passes_its_own_smoke_suite(self, tmp_path, monkeypatch):
        """Round trip in a subprocess (the export has its own `app` package,
        which would collide with the backend's inside this process)."""
        bp = _draft(monkeypatch)
        out = tmp_path / "export"
        generate_product(bp, out)

        env = dict(os.environ)
        env["PYTHONPATH"] = str(out)
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_smoke.py", "-q"],
            cwd=str(out),
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert proc.returncode == 0, (
            "generated smoke suite failed:\n" + proc.stdout[-3000:] + proc.stderr[-2000:]
        )


# --- 2. Drafting-mode honesty ------------------------------------------------


class TestDraftingModeIsVisible:
    def test_keyword_fallback_is_labelled(self):
        bp = draft_blueprint_from_brief(BRIEF, use_llm=False)
        assert bp.drafting_mode == "keyword_fallback"
        assert "disabled" in (bp.drafting_note or "")

    def test_llm_failure_reason_travels_on_the_blueprint(self, monkeypatch):
        def boom(brief, *, vertical_hint=None):
            raise TimeoutError("credit died mid-call")

        monkeypatch.setattr(
            "app.factory.product_architect._draft_with_llm", boom
        )
        bp = draft_blueprint_from_brief(BRIEF, use_llm=True)
        assert bp.drafting_mode == "keyword_fallback"
        assert "TimeoutError" in (bp.drafting_note or "")

    def test_llm_success_is_labelled_architect(self, monkeypatch):
        real = draft_blueprint_from_brief(BRIEF, use_llm=False)
        real.drafting_mode = None
        real.drafting_note = None

        monkeypatch.setattr(
            "app.factory.product_architect._draft_with_llm",
            lambda brief, *, vertical_hint=None: real,
        )
        bp = draft_blueprint_from_brief(BRIEF, use_llm=True)
        assert bp.drafting_mode == "architect_llm"

    def test_chat_summary_names_the_drafter(self, monkeypatch):
        state = SessionState(
            session_id="sess-honesty", user_id="u1", account_id="a1"
        )
        state.product_design = ProductDesignState()
        monkeypatch.setenv("ARCHITECT_LLM_DRAFTING_ENABLED", "0")
        result = platform_chat_flow.draft_from_chat(state, BRIEF)
        assert result["drafting_mode"] == "keyword_fallback"
        assert "deterministic templates" in result["summary"].lower()


# --- 3. Coder: GENERATE capabilities ------------------------------------------


GOOD_BODY = (
    'action = (arguments or {}).get("action", "status")\n'
    'if action == "status":\n'
    '    return {"ok": True, "records": len(_STATE)}\n'
    'if action == "create":\n'
    '    rid = str(len(_STATE) + 1)\n'
    '    _STATE[rid] = dict(arguments.get("record") or {})\n'
    '    return {"ok": True, "id": rid}\n'
    'return {"ok": False, "error": "unknown action: " + str(action)}'
)


class TestCoder:
    def test_validate_body_rejects_forbidden_constructs(self):
        with pytest.raises(CoderError):
            _validate_body("import os\nreturn {}", "cap")
        with pytest.raises(CoderError):
            _validate_body("eval('1')\nreturn {}", "cap")
        with pytest.raises(CoderError):
            _validate_body("return {unclosed", "cap")

    def test_coder_written_module_ships_and_runs(self, tmp_path, monkeypatch):
        bp = draft_blueprint_from_brief(BRIEF, use_llm=False)
        gen_caps = [
            c.id for c in bp.capabilities if (c.strategy_hint or "") == "GENERATE"
        ]
        assert gen_caps, "keyword drafting no longer emits a GENERATE core capability"

        monkeypatch.setenv(coder_mod.CODER_ENABLED_ENV, "1")
        indented = "\n".join("    " + ln for ln in GOOD_BODY.splitlines())
        monkeypatch.setattr(
            coder_mod,
            "generate_handler_body",
            lambda cap, blueprint: {"body": indented, "model": "test-coder"},
        )

        out = tmp_path / "export"
        result = generate_product(bp, out)

        assert result["coder"]["written"], "coder wrote nothing"
        cap_id = result["coder"]["written"][0]
        mod_path = out / "app" / "actions" / (cap_id.replace("-", "_") + ".py")
        text = mod_path.read_text(encoding="utf-8")
        assert "generated_logic" in text
        assert "test-coder" in text
        compile(text, str(mod_path), "exec")

    def test_coder_failure_ships_honest_stub_with_reason(self, tmp_path, monkeypatch):
        bp = draft_blueprint_from_brief(BRIEF, use_llm=False)
        monkeypatch.setenv(coder_mod.CODER_ENABLED_ENV, "1")

        def no_credit(cap, blueprint):
            raise CoderError("coder LLM failed: HTTPStatusError: 402 Payment Required")

        monkeypatch.setattr(coder_mod, "generate_handler_body", no_credit)

        out = tmp_path / "export"
        result = generate_product(bp, out)

        stubbed = result["coder"]["stubbed"]
        assert stubbed, "coder failure was not recorded"
        assert any("402" in reason for reason in stubbed.values())
        # The stub module still exists and is the honest kernel stub.
        cap_id = next(iter(stubbed))
        mod_path = out / "app" / "actions" / (cap_id.replace("-", "_") + ".py")
        assert "dependency_required" in mod_path.read_text(encoding="utf-8").lower() or (
            "DEPENDENCY_REQUIRED" in mod_path.read_text(encoding="utf-8")
        )

    def test_coder_disabled_is_recorded_not_silent(self, tmp_path, monkeypatch):
        bp = draft_blueprint_from_brief(BRIEF, use_llm=False)
        monkeypatch.setenv(coder_mod.CODER_ENABLED_ENV, "0")
        out = tmp_path / "export"
        result = generate_product(bp, out)
        assert any(
            "disabled" in reason for reason in result["coder"]["stubbed"].values()
        )

    def test_approve_summary_discloses_stubbed_capabilities(self, tmp_path, monkeypatch):
        state = SessionState(session_id="sess-coder", user_id="u1", account_id="a1")
        state.product_design = ProductDesignState()
        monkeypatch.setenv("ARCHITECT_LLM_DRAFTING_ENABLED", "0")
        monkeypatch.setenv(coder_mod.CODER_ENABLED_ENV, "0")
        platform_chat_flow.draft_from_chat(state, BRIEF)
        result = platform_chat_flow.approve_and_generate(
            state, output_root=tmp_path / "gen"
        )
        assert "honest stubs" in result["summary"], result["summary"]
