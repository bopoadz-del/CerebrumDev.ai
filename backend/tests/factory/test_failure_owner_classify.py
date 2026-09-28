"""Who owns a red TESTER suite decides whether a rework round can even run.

Two live failures on Cerebrum VenueOps (sess_42d244d317f042b2) exposed two
bugs in the classifier, both of which halted a run the writer could have fixed:

1. ``AssertionError: ('open_items', '1', 1)`` -- the product stored an int and
   read back a string (model says int, migration says String(512)). The assert
   fired inside tests/test_models.py, so ``innermost`` was the test file, so it
   was billed FACTORY and no rework ran. But a factory assertion failing on
   product output IS a product failure.

2. That same run also failed ``database is locked`` inside app/store.py -- a
   genuine product/product-owned row. It was discarded because one
   factory-owned row made the WHOLE verdict FACTORY (``if factory_owned:``).

The rule the docstring already states: PRODUCT includes a factory-written test
whose failure is the product's doing. These tests hold the code to it.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.factory.build.failure_owner import FACTORY, PRODUCT, classify

FACTORY_FILES = (
    "tests/test_models.py",
    "tests/test_routes.py",
    "tests/test_data_lifecycle.py",
)


def _verdict(findings, rows=None, reason="suite_red"):
    payload = {"failing_tests": rows} if rows is not None else {}
    return SimpleNamespace(findings=findings, payload=payload, reason=reason, gate="suite_green")


def test_assertion_in_a_factory_test_is_the_products_failure():
    """open_items: the product returned a wrong value; the writer can fix it."""
    v = _verdict(
        ["FAILED tests/test_models.py::test_every_model_round_trips - AssertionError: ('open_items', '1', 1)"]
    )
    assert classify(v, FACTORY_FILES)["owner"] == PRODUCT


def test_a_broken_factory_test_is_still_the_factorys():
    """KeyError 'figure': the miner invented a field; the test itself is wrong."""
    v = _verdict(
        ["FAILED tests/test_models.py::test_every_model_round_trips - KeyError: 'figure'"]
    )
    assert classify(v, FACTORY_FILES)["owner"] == FACTORY


def test_one_factory_row_does_not_poison_a_product_row():
    """The lock failure is product-owned; a KeyError row must not bury it."""
    v = _verdict(
        [
            "FAILED tests/test_data_lifecycle.py::test_parallel_writes_match_fastapi_threadpool - sqlite3.OperationalError: database is locked",
            "FAILED tests/test_models.py::test_every_model_round_trips - KeyError: 'figure'",
        ],
        rows=[
            {"nodeid": "tests/test_data_lifecycle.py::test_parallel_writes_match_fastapi_threadpool",
             "file": "tests/test_data_lifecycle.py", "name": "test_parallel_writes_match_fastapi_threadpool",
             "kind": "error", "message": "database is locked",
             "innermost": "app/store.py:41 in connect"},
            {"nodeid": "tests/test_models.py::test_every_model_round_trips",
             "file": "tests/test_models.py", "name": "test_every_model_round_trips",
             "kind": "error", "message": "KeyError: 'figure'", "innermost": "tests/test_models.py:31"},
        ],
    )
    assert classify(v, FACTORY_FILES)["owner"] == PRODUCT


def test_a_product_raise_in_a_factory_test_stays_product():
    v = _verdict(
        ["FAILED tests/test_routes.py::test_every_capability_route_answers - RuntimeError: boom"],
        rows=[{"nodeid": "tests/test_routes.py::x", "file": "tests/test_routes.py", "name": "x",
               "kind": "error", "message": "RuntimeError", "innermost": "app/routes.py:88 in handle"}],
    )
    assert classify(v, FACTORY_FILES)["owner"] == PRODUCT


def test_pure_factory_failure_still_halts_and_names_the_generator():
    v = _verdict(
        ["FAILED tests/test_models.py::test_every_model_round_trips - KeyError: 'figure'"]
    )
    out = classify(v, FACTORY_FILES)
    assert out["owner"] == FACTORY
    assert "roles_handlers.py" in out["generator"]


def test_environment_fault_is_unchanged():
    v = _verdict([], reason="environment_fault")
    assert classify(v, FACTORY_FILES)["owner"] == "ENVIRONMENT"


def test_a_broken_stub_assertion_stays_factory():
    """`assert False` is a broken test the factory wrote, not a product defect.

    It carries no value comparison -- no (key, got, want) tuple, no ==. It must
    stay FACTORY so the run halts with zero rework (the g_series invariant), not
    dispatch a writer that cannot fix a test it may not edit.
    """
    v = _verdict(
        ["FAILED tests/test_zz_broken_generated.py::test_zz_broken_generated - AssertionError: broken on purpose"],
        rows=[{"nodeid": "tests/test_zz_broken_generated.py::test_zz_broken_generated",
               "file": "tests/test_zz_broken_generated.py", "name": "test_zz_broken_generated",
               "kind": "failure", "message": "broken on purpose",
               "text": "def test_zz_broken_generated():\n>   assert False, 'broken on purpose'\nE   AssertionError: broken on purpose",
               "innermost": "tests/test_zz_broken_generated.py:2"}],
    )
    assert classify(v, ("tests/test_zz_broken_generated.py",))["owner"] == FACTORY


# ── v3: ownership by construction, not by message shape ────────────────────
#
# When the runner supplies behavior_test_files (the tests run_tester's own
# emitters stamped), an AssertionError in one of them is the PRODUCT failing
# a behavior check BY CONSTRUCTION -- no tuple/== sniffing. Live miss the
# heuristics could not catch: the negative floor's
#   "mega_event_history_explorer accepted a payload with no question: {json}"
# asserts via `status_code in (...) or _refused(resp)` -- no ==, no tuple --
# and was billed FACTORY, halting a run a writer could have advanced.

BEHAVIOR = FACTORY_FILES


def test_behavior_assertion_is_product_even_without_comparison_shape():
    v = _verdict(
        ["FAILED tests/test_data_lifecycle.py::test_x_refuses_a_missing_required_field - AssertionError: x accepted a payload with no question: {\"id\":2}"],
        rows=[{"nodeid": "tests/test_data_lifecycle.py::test_x", "file": "tests/test_data_lifecycle.py",
               "name": "test_x", "kind": "failure",
               "message": 'AssertionError: x accepted a payload with no question: {"id":2}',
               "innermost": "tests/test_data_lifecycle.py:40"}],
    )
    out = classify(v, FACTORY_FILES, behavior_test_files=BEHAVIOR)
    assert out["owner"] == PRODUCT


def test_injected_stub_assertion_stays_factory_under_behavior_list():
    """The g_series stub: written during the TESTER phase (so inside
    factory_test_files) but NOT by run_tester's emitters (so outside the
    behavior list). Its assertion is nobody's product."""
    stub = "tests/test_zz_broken_generated.py"
    v = _verdict(
        [f"FAILED {stub}::test_zz - AssertionError: broken on purpose"],
        rows=[{"nodeid": f"{stub}::test_zz", "file": stub, "name": "test_zz",
               "kind": "failure", "message": "AssertionError: broken on purpose",
               "innermost": f"{stub}:2"}],
    )
    out = classify(v, FACTORY_FILES + (stub,), behavior_test_files=BEHAVIOR)
    assert out["owner"] == FACTORY


def test_nonassertion_error_in_behavior_test_stays_factory():
    """KeyError 'figure' in a behavior file is the TEST code breaking (a
    mined junk field), never the product."""
    v = _verdict(
        ["FAILED tests/test_models.py::test_every_model_round_trips - KeyError: 'figure'"],
        rows=[{"nodeid": "tests/test_models.py::t", "file": "tests/test_models.py",
               "name": "test_every_model_round_trips", "kind": "error",
               "message": "KeyError: 'figure'", "innermost": "tests/test_models.py:31"}],
    )
    out = classify(v, FACTORY_FILES, behavior_test_files=BEHAVIOR)
    assert out["owner"] == FACTORY
