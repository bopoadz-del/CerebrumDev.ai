"""A library that raises under a product call is the product's failure
(misclassification, release cycle 9 smoke B).

Live: smoke B (sess_e8aa8ad93a3a4d91, build/plt_f9c91b85bce748c5): the
Factory's tests/test_data_lifecycle.py::test_parallel_writes_match_fastapi_threadpool
failed with ``sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow
10 reached``. Reproduced on the WRITER checkpoint: the stack runs
tests/test_data_lifecycle.py -> app/store.py:237 save -> app/store.py:247 get ->
app/store.py:159 _core_connection -> sqlalchemy pool (save holds a pooled
connection and checks out a second; 20 threads starve a 5+10 pool). The
classifier read only the INNERMOST frame -- inside sqlalchemy, neither app/ nor
an assertion -- billed it FACTORY_FAULT and stopped the build with the writer
never told. The product was on the stack: a library raised under a product
call, so the writer can fix it, and the row must route PRODUCT.

A failure whose stack never enters app/ (the test code itself broke) stays the
Factory's -- nothing here widens that.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.factory.build.failure_owner import FACTORY, PRODUCT, classify

SUITE = "tests/test_data_lifecycle.py"
LIB = "/usr/local/lib/python3.11/site-packages/sqlalchemy/pool/impl.py"

TRACE = f"""{SUITE}:171: in test_parallel_writes
    results = [f.result() for f in as_completed(futures)]
/usr/local/lib/python3.11/concurrent/futures/_base.py:449: in result
    return self.__get_result()
{SUITE}:167: in _write
    return store.save(ENTITY, payload, tenant_id=TENANT)
app/store.py:237: in save
    stored = get(entity, new_id, tenant_id)
app/store.py:159: in _core_connection
    return db.engine().connect()
{LIB}:167: TimeoutError
"""


def _verdict(rows):
    return SimpleNamespace(reason="suite_red", payload={"failing_tests": rows}, findings=[], detail="")


def _row(frames):
    return {
        "nodeid": f"{SUITE}::test_parallel_writes", "file": SUITE, "name": "test_parallel_writes",
        "kind": "failure", "message": "sqlalchemy.exc.TimeoutError: QueuePool limit",
        "exc_type": "TimeoutError", "innermost": frames[-1], "frames": frames,
    }


def test_a_library_raising_under_a_product_call_is_the_products():
    frames = [SUITE, "/usr/local/lib/python3.11/concurrent/futures/_base.py", SUITE,
              "app/store.py", "app/store.py", LIB]
    out = classify(_verdict([_row(frames)]), [SUITE], behavior_test_files=[SUITE])
    assert out["owner"] == PRODUCT, out
    out = classify(_verdict([_row(frames)]), [SUITE])
    assert out["owner"] == PRODUCT, out


def test_a_library_raising_under_the_test_alone_stays_the_factorys():
    frames = [SUITE, LIB]
    out = classify(_verdict([_row(frames)]), [SUITE], behavior_test_files=[SUITE])
    assert out["owner"] == FACTORY, out


def test_the_junit_row_and_the_verdict_carry_every_frame(tmp_path):
    from app.factory.build.gates import failing_tests_from_junit

    junit = tmp_path / "junit.xml"
    from xml.sax.saxutils import escape

    junit.write_text(
        '<?xml version="1.0"?><testsuites><testsuite>'
        '<testcase classname="tests.test_data_lifecycle" name="test_parallel_writes">'
        f'<failure message="sqlalchemy.exc.TimeoutError: QueuePool" type="sqlalchemy.exc.TimeoutError">{escape(TRACE)}</failure>'
        "</testcase></testsuite></testsuites>",
        encoding="utf-8",
    )
    (rows,) = failing_tests_from_junit(tmp_path, junit)
    assert "app/store.py" in rows["frames"]
    assert rows["innermost"] == LIB


def test_the_suite_verdict_payload_keeps_the_frames():
    import inspect

    from app.factory.build import gates

    src = inspect.getsource(gates)
    i = src.index('"failing_tests": [')
    assert '"frames"' in src[i:i + 600]
    assert Path(gates.__file__).is_file()
