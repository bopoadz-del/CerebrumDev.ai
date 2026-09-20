"""What a pilot serves must drive the product, and the prompt says so.

A pilot is handed to a DevOps team to deploy and test. The factory's own
default console was `<p>Generated platform UI.</p>` -- a page whose only job
was to make the ui_served_200 check go green. No writer could start above
that. FinOps (sess_065fc3eac75c4f62) also shipped a React app nothing builds:
no node step in its Dockerfile, so the real UI was dead source.

The bar is stated in the writer prompt (v8) and enforced by the
ui_end_to_end gate, so the agent knows it before writing rather than after a
rework round.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.factory.build.store_acceptance import render_ui_index
from app.factory.build.ui_e2e import _render_probe
from app.factory.build.writer_prompt import render_writer_prompt


class _Blueprint:
    product_id = "p"
    product_name = "Acme Ops"
    vertical = "ops"
    summary = "s"


class TestTheDefaultConsoleDrivesTheProduct:
    def test_it_discovers_capabilities_instead_of_hardcoding_them(self):
        html = render_ui_index("Acme Ops")
        assert "/v1/capabilities" in html
        assert '"/v1/" + cap' in html, "routes must be built from what it discovers"

    def test_it_can_create_and_list_a_record(self):
        html = render_ui_index("Acme Ops")
        assert 'method: "POST"' in html
        assert 'data-act="list"' in html

    def test_it_takes_a_token_rather_than_embedding_one(self):
        html = render_ui_index("Acme Ops")
        assert 'id="tok"' in html and "Bearer" in html
        assert "dev-local-token" not in html

    def test_it_shows_the_authority_label_an_answer_carries(self):
        html = render_ui_index("Acme Ops")
        assert "LABEL_KEYS" in html
        for key in ("label", "authority", "precedence"):
            assert f'"{key}"' in html

    def test_it_offers_only_surfaces_the_product_declares(self):
        """It used to call /v1/rag/query unconditionally, which 404s on a
        product with no RAG surface and failed that build."""
        html = render_ui_index("Acme Ops")
        assert "/openapi.json" in html
        assert '$("ask").hidden = !RAG' in html

    def test_it_is_not_the_placeholder_it_replaced(self):
        html = render_ui_index("Acme Ops")
        assert "Generated platform UI." not in html
        assert len(html) > 2000, "a console, not a banner"

    def test_it_carries_the_product_name(self):
        assert "Acme Ops" in render_ui_index("Acme Ops")


class TestThePromptStatesTheBarTheGateEnforces:
    def _prompt(self) -> str:
        return render_writer_prompt(_Blueprint(), brief="build it")

    def test_the_prompt_states_the_ui_contract(self):
        text = self._prompt()
        assert "UI (a pilot is deployed and tested" in text
        assert "at least two capabilities driven from" in text
        assert "authority label" in text

    def test_the_prompt_states_the_one_ui_rule(self):
        text = self._prompt()
        assert "The platform serves ONE UI" in text
        assert "A UI nothing builds is decoration" in text

    def test_the_gate_is_wired_into_the_writer_contract(self):
        import inspect

        from app.factory.build import gates

        src = inspect.getsource(gates.gate_writer_contract)
        assert "gate_ui_end_to_end(ctx)" in src
        assert "if not ui_live.ok" in src


class TestTheGateJudgesWhatIsServed:
    def _probe_findings(self, tmp_path: Path, html: str, openapi: dict | None = None) -> dict:
        """Run the gate's own probe body against a fake product tree."""
        import subprocess
        import sys

        (tmp_path / "app" / "static").mkdir(parents=True)
        (tmp_path / "app" / "static" / "index.html").write_text(html, encoding="utf-8")
        (tmp_path / "app" / "actions").mkdir(parents=True, exist_ok=True)
        for cap in ("alpha", "beta"):
            (tmp_path / "app" / "actions" / f"{cap}.py").write_text("", encoding="utf-8")
        (tmp_path / "app" / "main.py").write_text(
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n"
            "\n"
            '@app.get(\"/v1/capabilities\")\n'
            "def caps():\n"
            '    return {\"items\": [\"alpha\", \"beta\"]}\n',
            encoding="utf-8",
        )
        if openapi is not None:
            (tmp_path / "openapi.json").write_text(json.dumps(openapi), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-c", _render_probe()],
            cwd=tmp_path, capture_output=True, text=True,
        )
        line = [ln for ln in proc.stdout.splitlines() if ln.startswith("UI_PROBE=")]
        assert line, proc.stdout + proc.stderr
        return json.loads(line[-1].split("=", 1)[1])

    def test_a_banner_is_refused(self, tmp_path):
        out = self._probe_findings(
            tmp_path, "<html><body><h1>Platform</h1><p>Generated platform UI.</p></body></html>"
        )
        assert any("drives 0 of 2" in f for f in out["findings"]), out

    def test_a_console_that_discovers_capabilities_passes(self, tmp_path):
        html = (
            "<html><body><script>"
            "fetch('/v1/capabilities');"
            'const u = "/v1/" + cap;'
            "</script></body></html>"
        )
        out = self._probe_findings(tmp_path, html)
        assert out["findings"] == [], out
        assert sorted(out["ui_caps"]) == ["alpha", "beta"]

    def test_an_optional_surface_the_product_lacks_is_not_a_broken_route(self, tmp_path):
        html = (
            "<html><body><script>"
            "fetch('/v1/capabilities'); fetch('/v1/rag/query');"
            'const u = "/v1/" + cap;'
            "</script></body></html>"
        )
        out = self._probe_findings(
            tmp_path, html, openapi={"paths": {"/v1/capabilities": {}, "/v1/alpha": {}}}
        )
        assert not any("rag" in f for f in out["findings"]), out

    def test_no_served_page_at_all_is_refused(self, tmp_path):
        (tmp_path / "app").mkdir()
        (tmp_path / "app" / "main.py").write_text("app = None\n", encoding="utf-8")
        import subprocess, sys

        proc = subprocess.run(
            [sys.executable, "-c", _render_probe()], cwd=tmp_path, capture_output=True, text=True
        )
        line = [ln for ln in proc.stdout.splitlines() if ln.startswith("UI_PROBE=")]
        out = json.loads(line[-1].split("=", 1)[1])
        assert any("serves no UI" in f for f in out["findings"]), out
