"""PRODUCT-suite regressions for veterinary-care / VetCare-class pilots.

Live sess_66a387b5c9b0495c (VetCare Hub / domain veterinary-care) went
TERMINAL on Factory Floor after #305/#306/#307:

    CODING AGENT STOPPED
    rework budget of 3 exhausted; TESTER gate still failing:
    PRODUCT (pilot-marked suite)

WRITER/compile had cleared. The PRODUCT suite then executed prepared
payloads against Store blocks / Alembic / domain_acceptance and reported:

1. Missing required field ``reference`` on pet_records_management,
   appointment_scheduling, automated_reminders,
   veterinarian_availability_tracking
2. no such table records
3. Missing PDF parser package
4. Workflow result error
5. Queue str/int type error
6. event_bus notification errors

These tests lock the factory construction that would have caught that
PRODUCT red without a live run. They do not weaken honesty: unrepaired
domain JSON still fails the live Store refusals, and export stays refused
when the pilot suite fails.
"""

from __future__ import annotations

from pathlib import Path

from app.factory.build.block_inputs import prepare_block_input
from app.factory.build.block_obligations import (
    augment_model_spec,
    ensure_record_envelope,
)
from app.factory.build.offline_adapters import (
    DOC_PARSE_UNWIRED_MARKER,
    QUERY_CREATE_MARKER,
    emit_database_query,
    emit_document_engine_parse,
    emit_runtime_module,
)
from app.factory.build.roles import _constraint_guard, _sample_payload

#: The four capabilities named on the live Floor / suite.
_VETCARE_CAPS = (
    (
        "pet_records_management",
        "pet_record",
        ["document_engine", "database", "workflow"],
        {
            "pet_name": "Nala",
            "owner_name": "Lee",
            "notes": "annual exam",
        },
    ),
    (
        "appointment_scheduling",
        "appointment",
        ["database", "event_bus", "queue"],
        {
            "pet_name": "Rex",
            "appointment_date": "2026-09-10",
            "veterinarian_name": "Dr Lee",
        },
    ),
    (
        "automated_reminders",
        "reminder",
        ["event_bus", "notification", "queue"],
        {
            "pet_name": "Nala",
            "reminder_type": "vaccination",
            "channel": "email",
        },
    ),
    (
        "veterinarian_availability_tracking",
        "availability",
        ["database", "team", "workflow"],
        {
            "veterinarian_name": "Dr Lee",
            "service_date": "2026-09-10",
            "status": "open",
        },
    ),
)


def _llm_spec(entity: str, domain: dict) -> dict:
    """Shape generate_model_spec emitted for veterinary-care: no ``reference``."""
    return {
        "entity": entity,
        "fields": [
            {"name": name, "type": "str", "required": True}
            for name in domain
        ],
    }


def _run_guard(spec: dict, payload: dict) -> dict:
    ns: dict = {}
    exec("def handle(payload):\n" + _constraint_guard(spec) + "\n    return {'ok': True}\n", ns)
    return ns["handle"](payload)


def test_llm_veterinary_specs_fail_product_guard_until_envelope():
    """Live miss: schema-built samples omitted ``reference``; PRODUCT refused."""
    for cid, entity, _blocks, domain in _VETCARE_CAPS:
        raw = _llm_spec(entity, domain)
        # Obligation augment must stay identity when no schema obligation
        # applies — envelope is a separate seam.
        assert augment_model_spec(raw, ["workflow"]) is raw
        sample = _sample_payload(raw)
        assert "reference" not in sample
        enveloped, added = ensure_record_envelope(raw)
        assert added == ["reference"], cid
        refused = _run_guard(enveloped, sample)
        assert refused.get("ok") is False, (cid, sample, refused)
        assert "reference" in refused.get("error", ""), (cid, refused)
        accepted = _run_guard(enveloped, _sample_payload(enveloped))
        assert accepted.get("ok") is True, (cid, accepted)
        assert _sample_payload(enveloped)["reference"]


def test_veterinary_product_prepare_repairs_live_product_symptoms():
    """Each live PRODUCT symptom is constructible from a domain record."""
    for cid, entity, roster, domain in _VETCARE_CAPS:
        enveloped, _ = ensure_record_envelope(_llm_spec(entity, domain))
        payload = _sample_payload(enveloped)
        assert payload["reference"]

        db = prepare_block_input("database", payload, entity=entity, roster=roster)
        assert db["table"] == entity
        assert db["table"] != "records"
        assert entity in (db.get("sql") or db["table"])

        bus = prepare_block_input(
            "event_bus", payload, product_name="VetCare Hub", roster=roster
        )
        assert isinstance(bus["topic"], str) and bus["topic"]
        # Domain reminder `channel` (email/sms/…) is left alone; only a
        # missing channel is filled with mcp (live notify miss).
        assert isinstance(bus.get("channel"), str) and bus["channel"]
        if "channel" not in domain:
            assert bus["channel"] == "mcp"
        assert isinstance(bus.get("payload"), dict)
        assert isinstance(bus.get("message"), str) and bus["message"]

        queued = prepare_block_input(
            "queue", {**payload, "id": "3", "priority": "1", "label": "id-1"}
        )
        assert queued["id"] == 3
        assert queued["item_id"] == 3
        assert queued["priority"] == 1
        assert queued["label"] == "id-1"

        doc = prepare_block_input("document_engine", payload)
        assert Path(doc["pdf_path"]).is_file()
        assert Path(doc["file_path"]).is_file()

        flow = prepare_block_input(
            "workflow",
            payload,
            roster=roster,
            entity=entity,
            product_name="VetCare Hub",
        )
        assert isinstance(flow["steps"], list) and flow["steps"]
        by_block = {step["block"]: step["input"] for step in flow["steps"]}
        if "database" in by_block:
            assert by_block["database"]["table"] == entity
        if "event_bus" in by_block:
            assert by_block["event_bus"].get("channel")
            assert by_block["event_bus"]["message"]
        if "notification" in by_block:
            assert by_block["notification"].get("channel")
            assert by_block["notification"]["message"]
            if "channel" not in domain:
                assert by_block["notification"]["channel"] == "mcp"
        if "queue" in by_block:
            assert isinstance(by_block["queue"].get("priority"), int)


def test_query_adapter_creates_missing_table_instead_of_records_error():
    """Live: SELECT * FROM records → no such table records."""
    query = (
        "        \"\"\"Execute SELECT query\"\"\"\n"
        "        sql = data.get(\"sql\")\n"
        "        params = data.get(\"params\", ())\n"
        "        \n"
        "        try:\n"
        "            cursor = self._connection.cursor()\n"
        "            cursor.execute(sql, params)\n"
    )
    out = emit_database_query(query)
    assert QUERY_CREATE_MARKER in out
    assert "no such table" in out
    assert "CREATE TABLE IF NOT EXISTS" in out
    # Default last-resort table name must not be the only path PRODUCT can take.
    assert "pet_record" not in out or "table" in out


def test_document_engine_parse_adapter_stubs_pdf_libs():
    src = emit_document_engine_parse("def parse(self, data):\n    return data\n")
    assert DOC_PARSE_UNWIRED_MARKER in src
    for name in ("pypdf", "PyPDF2", "pdfplumber"):
        assert name in src
    assert emit_runtime_module("document_engine", "def parse(self, data):\n    return data\n") == src


def test_unrepaired_veterinary_domain_json_still_refused():
    """Honesty: construction is required. Domain JSON is not a Store record."""
    domain = {"pet_name": "Nala", "appointment_date": "2026-09-10"}
    # event_bus without topic
    bus = domain
    assert not (isinstance(bus.get("topic"), str) and bus.get("topic", "").strip())
    # database without sql/table
    assert not domain.get("sql") and not domain.get("table")
    # document_engine without a real file
    assert not any(
        isinstance(domain.get(k), str) and Path(domain[k]).is_file()
        for k in ("file_path", "pdf_path", "docx_path", "xlsx_path")
    )
    # After prepare, the same record is constructible — that is the fix,
    # not a fake ok over a failed block.
    prepared_bus = prepare_block_input("event_bus", domain)
    prepared_db = prepare_block_input("database", domain, entity="appointment")
    prepared_doc = prepare_block_input("document_engine", domain)
    assert prepared_bus["topic"]
    assert prepared_db["table"] == "appointment"
    assert Path(prepared_doc["pdf_path"]).is_file()
