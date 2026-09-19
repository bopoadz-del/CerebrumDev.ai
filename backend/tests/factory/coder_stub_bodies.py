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
    # Shape the payload per block from the block's OWN manifest, the way a
    # real agent does. The body used to forward the raw capability payload to
    # every block, which only an always-ok fake accepts: the Store's real
    # estate_registry answers "missing required field(s): id" and "unknown
    # field(s): reference". The Factory holds no blocks, so the stub has to
    # be good enough for the Store's.
    return (
        "    import json as _json\n"
        "    from pathlib import Path as _Path\n"
        "\n"
        "    def _for_block(block_id):\n"
        "        manifest = (\n"
        "            _Path(__file__).resolve().parents[2]\n"
        "            / 'vendor' / 'blocks' / block_id / 'block.json'\n"
        "        )\n"
        "        try:\n"
        "            inputs = _json.loads(\n"
        "                manifest.read_text(encoding='utf-8')\n"
        "            ).get('inputs') or []\n"
        "        except (OSError, ValueError):\n"
        "            return payload\n"
        "        named = [\n"
        "            i for i in inputs\n"
        "            if isinstance(i, dict)\n"
        "            and i.get('name') not in (None, 'action', 'input')\n"
        "        ]\n"
        "        if not named:\n"
        "            return payload\n"
        "        shaped = {}\n"
        "        for spec in named:\n"
        "            name = spec['name']\n"
        "            if name in payload:\n"
        "                shaped[name] = payload[name]\n"
        "            elif spec.get('required'):\n"
        "                shaped[name] = (\n"
        "                    dict(payload) if spec.get('type') == 'json' else 'sample'\n"
        "                )\n"
        "        return shaped\n"
        "\n"
        "    results = {}\n"
        "    for block_id in BLOCK_IDS:\n"
        "        results[block_id] = execute(\n"
        "            block_id, _for_block(block_id),\n"
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
