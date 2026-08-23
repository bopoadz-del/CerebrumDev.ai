"""Handler bodies for tests that stand in for coder output.

A stub is not free to return anything. The WRITER gate refuses a handler
that declares blocks in ``BLOCK_IDS`` and never calls them — that is F11,
and it is the shape LotDesk shipped, where ``capture`` and ``team`` were
bound and never invoked. A stub returning a canned dict has exactly that
defect, so using one would assert the factory accepts what it must reject.

These bodies invoke every declared block and fail closed on a block error,
which is what a correct coder handler does. Anything a test needs to assert
about coder wiring rides along in ``marker``, which is spliced into the
success envelope literal so a header search for it still matches.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def invoking_handler_body(marker: Optional[Dict[str, Any]] = None) -> str:
    """A handler body that calls every block in BLOCK_IDS and fails closed.

    ``marker`` adds keys to the success envelope, written inline so the
    emitted source literally contains e.g. ``"agent": True`` — tests grep the
    generated handler for their own marker.
    """
    extras = "".join(
        f'            "{key}": {value!r},\n' for key, value in (marker or {}).items()
    )
    return (
        "    results = {}\n"
        "    for block_id in BLOCK_IDS:\n"
        "        results[block_id] = execute(\n"
        "            block_id, payload,\n"
        "            action=BLOCK_DEFAULT_ACTIONS.get(block_id),\n"
        "        )\n"
        "    failed = {\n"
        "        b: r for b, r in results.items()\n"
        '        if isinstance(r, dict) and r.get("status") == "error"\n'
        "    }\n"
        "    if failed:\n"
        '        return {"ok": False, "capability": CAPABILITY_ID,\n'
        '                "error": str(failed)[:200]}\n'
        "    return {\n"
        '            "ok": True,\n'
        '            "capability": CAPABILITY_ID,\n'
        '            "results": results,\n'
        + extras
        + "    }"
    )
