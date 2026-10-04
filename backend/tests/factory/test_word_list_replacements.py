"""Each former word list is replaced by the mechanism it approximated.

One test per replacement, with invented names, so a list can never quietly
come back: if the words decided again, these would pass for the wrong
reason or fail.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.factory.build import acceptance_floor as floor
from app.factory.build.block_inputs import required_fields_from_rosters
from app.factory.build.level_grade import _cli_honesty_miss
from app.factory.build.store_acceptance import demote_if_advisory
from app.factory.build.supply_chain import _text_has_outbound


def _cap(cid: str, description: str) -> SimpleNamespace:
    return SimpleNamespace(id=cid, description=description, block_ids=[cid])


# _RETRIEVAL_HINTS -> withheld signal (block bindings, no signing key)


def test_a_retrieval_word_in_the_brief_raises_no_signal():
    brief = SimpleNamespace(
        rigor="",
        summary="semantic search over a knowledge corpus of documents",
        capabilities=[_cap("zorblat_rag_search", "vector retrieval ingest")],
        connectors=[],
    )
    assert "retrieval" not in floor.brief_signals(brief)


def test_the_retrieval_check_reads_withheld_with_its_reason_never_a_veto():
    assert "retrieval" in floor.withheld_signals()
    label = floor.withheld_label("rag_roundtrip_hit")
    assert label == "WITHHELD(no signing key)"
    assert "rag_roundtrip_hit" in floor.advisory_ids(None)
    assert "rag_roundtrip_hit" not in floor.enforced_ids(None)
    status, detail = demote_if_advisory("rag_roundtrip_hit", "FAIL", "no hit")
    assert (status, detail) == ("SKIP", "WITHHELD(no signing key): no hit")
    assert demote_if_advisory("rag_roundtrip_hit", "PASS", "hit") == ("PASS", "hit")


# inline "./" -> path structure


def test_a_rendered_path_is_matched_by_its_segments_wherever_it_is_mounted():
    rendered = frozenset({"app/zorblat.py"})
    assert floor._factory_rendered("./app/zorblat.py", rendered)
    assert floor._factory_rendered("/srv/quux/app/zorblat.py", rendered)
    assert not floor._factory_rendered("zorblat.py", rendered)  # bare basename
    assert not floor._factory_rendered("lib/app/zorblat.py", rendered)  # relative, deeper


# _ROSTER_CONSULTS_PAYLOAD -> AST: the loop looks its variable up in payload


def test_a_roster_is_one_whose_loop_consults_the_payload():
    consults = (
        'FROBS = ["zorb_ref", "quux_id"]\n'
        "def handle(payload):\n"
        "    for name in FROBS:\n"
        "        if name not in payload:\n"
        "            raise ValueError('needs ' + name)\n"
    )
    words_only = (
        'FROBS = ["zorb_ref", "quux_id"]\n'
        "def handle(payload):\n"
        "    for name in FROBS:\n"
        "        log('missing not in payload .get(')\n"
    )
    assert required_fields_from_rosters(consults) == ["zorb_ref", "quux_id"]
    assert required_fields_from_rosters(words_only) == []


# CLI_FOUNDING_HONESTY_MISS -> the receipt's typed blocker field


def test_any_named_receipt_blocker_is_a_miss_and_prose_is_not_read():
    named = {"coder_receipt": {"ok": True, "blocker": "ZORBLAT_NEW_BLOCKER"}}
    assert _cli_honesty_miss(named) == "ZORBLAT_NEW_BLOCKER"
    prose = {"coder_receipt": {"ok": True, "detail": "FACTORY_CODE_CLI_BILLING in words"}}
    assert _cli_honesty_miss(prose) is None


# _OUTBOUND_MARKERS -> outbound URL structure


def test_outbound_is_a_non_loopback_url_whatever_tool_or_host_names_it():
    assert _text_has_outbound("RUN zorbfetch https://quux.example/payload")
    assert not _text_has_outbound("RUN curl -fsS http://127.0.0.1:8000/health")
    assert not _text_has_outbound("# see https://quux.example for docs")
