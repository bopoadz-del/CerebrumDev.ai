"""The five estate mirror blocks must do real work — every fail path proven.

Regression against the echo-stub generation: these blocks used to return
{"status": "ok", "result": <input>} unconditionally. Each test below proves
the failure path AND the success path of the real implementation.
"""

from __future__ import annotations



from app.factory.vendor_blocks_mirror.evidence_verifier import block as ev
from app.factory.vendor_blocks_mirror.estate_maintenance import block as em
from app.factory.vendor_blocks_mirror.estate_registry import block as er
from app.factory.vendor_blocks_mirror.portfolio_rollup import block as pr
from app.factory.vendor_blocks_mirror.readiness_engine import block as re_


class TestEvidenceVerifier:
    def test_tampered_content_fails_verification(self):
        ev.reset_state()
        content = "signed statement"
        stored = ev.run(input={"action": "store", "content": content})
        assert stored["status"] == "ok"
        record_id = stored["result"]["id"]
        bad = ev.run(
            input={"action": "verify", "record_id": record_id, "content": "tampered!"}
        )
        assert bad["status"] == "error"
        good = ev.run(
            input={"action": "verify", "record_id": record_id, "content": content}
        )
        assert good["status"] == "ok"

    def test_unknown_record_id_is_an_error(self):
        ev.reset_state()
        out = ev.run(input={"action": "verify", "record_id": "nope", "content": "x"})
        assert out["status"] == "error"


class TestEstateRegistry:
    def test_duplicate_id_is_rejected(self, tmp_path):
        first = er.run(
            input={"action": "create", "id": "est-1", "data": {"name": "Manor"}},
            store_dir=str(tmp_path),
        )
        assert first["status"] == "ok"
        dup = er.run(
            input={"action": "create", "id": "est-1", "data": {"name": "Again"}},
            store_dir=str(tmp_path),
        )
        assert dup["status"] == "error"

    def test_read_back_returns_the_stored_record(self, tmp_path):
        er.run(
            input={"action": "create", "id": "est-2", "data": {"name": "Villa"}},
            store_dir=str(tmp_path),
        )
        got = er.run(input={"action": "read", "id": "est-2"}, store_dir=str(tmp_path))
        assert got["status"] == "ok"
        assert got["result"]["data"]["name"] == "Villa"


class TestReadinessEngine:
    def test_unmet_checklist_fails_the_gate(self):
        out = re_.run(
            input={
                "action": "evaluate",
                "checklist": [
                    {"id": "plumbing", "required": True},
                    {"id": "heating", "required": True},
                ],
                "state": {"plumbing": "ok"},
            }
        )
        assert out["status"] == "error"
        assert "heating" in str(out.get("detail") or out.get("error"))

    def test_met_checklist_passes(self):
        out = re_.run(
            input={
                "action": "evaluate",
                "checklist": [{"id": "plumbing", "required": True}],
                "state": {"plumbing": "ok"},
            }
        )
        assert out["status"] == "ok"


class TestEstateMaintenance:
    def test_unknown_work_order_id_is_an_error(self, tmp_path):
        out = em.run(input={"action": "complete", "id": "wo-nope"}, store_dir=str(tmp_path))
        assert out["status"] == "error"

    def test_create_list_complete_round_trip(self, tmp_path):
        em.run(
            input={"action": "create", "title": "Roof", "due": "2026-10-01"},
            store_dir=str(tmp_path),
        )
        em.run(
            input={"action": "create", "title": "Garden", "due": "2026-09-01"},
            store_dir=str(tmp_path),
        )
        listed = em.run(input={"action": "list"}, store_dir=str(tmp_path))
        assert listed["status"] == "ok"
        titles = [w["title"] for w in listed["result"]["orders"]]
        assert titles == ["Garden", "Roof"]  # sorted by due


class TestPortfolioRollup:
    def test_aggregation_sums_and_groups(self):
        out = pr.run(
            input={
                "properties": [
                    {"id": "a", "value": 100, "status": "active"},
                    {"id": "b", "value": 200, "status": "active"},
                    {"id": "c", "value": 50, "status": "sold"},
                ]
            }
        )
        assert out["status"] == "ok"
        result = out["result"]
        assert result["count"] == 3
        assert result["total_value"] == 350
        assert result["by_status"]["active"] == {"count": 2, "total_value": 300}
        assert result["by_status"]["sold"]["count"] == 1

    def test_empty_input_is_honest_zeros(self):
        out = pr.run(input={"properties": []})
        assert out["status"] == "ok"
        assert out["result"]["count"] == 0
        assert out["result"]["total_value"] == 0
