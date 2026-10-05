"""Capabilities that depend on a DECLARED placeholder connector.

A brief may ask for an external system the Store cannot supply ("honest
status-only stubs for <CRM> and <DMS> until we connect our own credentials").
The blueprint declares such systems in ``ProductBlueprint.connectors``, and
the product ships them as ``not_implemented`` stubs. A capability that calls
one declares it in ``CapabilitySpec.connectors``.

Live automotive build: such a capability refused, correctly, and the
generated suites expected it to accept and round-trip its own record, so
TESTER stopped on SAME_FAILURE_TWICE over an honest product. The expectation
now comes from the declaration, never from a capability's name:

* the product's capability route answers the TYPED unavailable refusal --
  HTTP 503, ``error_kind: "unavailable"`` (the Store's typed error kind and
  its status), naming each connector and the setting it needs;
* the generated route suite expects exactly that refusal for a declared
  capability, and the full answer for every other one;
* the round-trip probes record a declared capability as NOT JUDGEABLE, with
  the reason, instead of failing it -- a refusal from a capability that
  declared nothing is still a failure;
* the negative floor still applies: every counter-case is refused, the typed
  refusal counting as a refusal.

One module renders the product side (``app/placeholders.py``) and answers the
Factory side, so the two cannot disagree.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.factory.blueprint import connector_slug

#: The Store's typed error kind for "a dependency is not configured", and the
#: HTTP status it maps to (Cerebrum-Blocks app/core/http_errors.py).
UNAVAILABLE_KIND = "unavailable"
UNAVAILABLE_STATUS = 503

#: The setting a placeholder connector will read once it is built:
#: ``<CONNECTOR>`` upper-cased plus this suffix.
SETTING_SUFFIX = "_CREDENTIALS"

#: Where the product carries the declaration (Factory-written, never authored).
PRODUCT_MODULE = "app/placeholders.py"

#: The Factory-stamped test that is the authority on a declared capability's
#: answer. A writer-authored test that disagrees with it is a test defect.
CONTRACT_TEST = "tests/test_placeholder_contract.py"

#: While the suite runs, every declared refusal the product answers is
#: appended, as one JSON object per line, to the file this variable names
#: (``{"test": <nodeid>, "capability": <id>}``). The stamped conftest sets it
#: beside pytest's JUnit report, so the gate reads it with the verdict.
REFUSAL_LOG_ENV = "PLACEHOLDER_REFUSAL_LOG"
REFUSAL_LOG_SUFFIX = ".placeholder_refusals.jsonl"

#: The work-list kind for a writer test that demanded a live answer from a
#: declared placeholder capability.
TEST_DEFECT = "test_defect"


def setting_for(connector_id: str) -> str:
    return connector_slug(connector_id).upper() + SETTING_SUFFIX


def placeholder_connectors(blueprint: Any) -> Dict[str, List[str]]:
    """capability id -> the declared placeholder connectors it calls.

    A connector is a placeholder when the blueprint declares it in
    ``connectors`` (the not_implemented stubs). Only capabilities that call
    at least one appear; every other capability is expected to work.
    """
    declared = {
        connector_slug(c) for c in (getattr(blueprint, "connectors", None) or [])
    }
    declared.discard("")
    out: Dict[str, List[str]] = {}
    for cap in getattr(blueprint, "capabilities", None) or []:
        needs = [
            c
            for c in (getattr(cap, "connectors", None) or [])
            if connector_slug(c) in declared
        ]
        if needs:
            out[str(cap.id)] = sorted(dict.fromkeys(connector_slug(c) for c in needs))
    return out


def brief_lines(blueprint: Any) -> List[str]:
    """What the WRITER builds for each declared capability, stated plainly."""
    mapping = placeholder_connectors(blueprint)
    if not mapping:
        return []
    lines = [
        "## Placeholder connectors (declared, not built)",
        "",
        f"These capabilities call a connector the blueprint declares as a "
        f"placeholder. The Factory ships {PRODUCT_MODULE}; the capability's "
        f"POST route answers HTTP {UNAVAILABLE_STATUS} with error_kind "
        f'"{UNAVAILABLE_KIND}", naming each connector and the setting it '
        "needs, before the handler runs. Build the handler as it will work "
        "once the connector exists. The Factory ships "
        f"{CONTRACT_TEST}, the authority on these answers. In any test of yours "
        "that touches these capabilities, "
        f"assert that {UNAVAILABLE_STATUS} typed refusal and its settings; "
        "never assert that the record was accepted, stored or read back -- "
        "that round trip is not judgeable until the connector is built.",
        "",
    ]
    for cap_id, connectors in sorted(mapping.items()):
        lines.append(
            f"- {cap_id}: "
            + ", ".join(f"{c} (setting {setting_for(c)})" for c in connectors)
        )
    return lines


def render_product_module(blueprint: Any) -> str:
    """``app/placeholders.py``: the declaration, readable by the product,
    its routes, its tests and the Factory's probes."""
    mapping = placeholder_connectors(blueprint)
    return (
        '"""Capabilities whose connectors are declared placeholders.\n'
        "\n"
        "Written by the Factory from the blueprint. A capability listed here\n"
        "answers the typed unavailable refusal until its connector is built;\n"
        "every other capability is expected to work.\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "import json\n"
        "import os\n"
        "from typing import Any, Dict, List, Optional\n"
        "\n"
        f"UNAVAILABLE_KIND = {UNAVAILABLE_KIND!r}\n"
        f"REFUSAL_LOG_ENV = {REFUSAL_LOG_ENV!r}\n"
        f"UNAVAILABLE_STATUS = {UNAVAILABLE_STATUS!r}\n"
        f"SETTING_SUFFIX = {SETTING_SUFFIX!r}\n"
        f"PLACEHOLDER_CONNECTORS: Dict[str, List[str]] = {mapping!r}\n"
        "\n"
        "\n"
        "def setting_for(connector_id: str) -> str:\n"
        "    return connector_id.upper() + SETTING_SUFFIX\n"
        "\n"
        "\n"
        "def _record(capability_id: str) -> None:\n"
        '    """Under the Factory suite only: note which running test this\n'
        '    refusal answered (pytest names it in PYTEST_CURRENT_TEST)."""\n'
        "    path = os.environ.get(REFUSAL_LOG_ENV)\n"
        "    if not path:\n"
        "        return\n"
        '    running = os.environ.get("PYTEST_CURRENT_TEST") or ""\n'
        '    test = running.rsplit(" (", 1)[0]\n'
        "    try:\n"
        '        with open(path, "a", encoding="utf-8") as fh:\n'
        '            fh.write(json.dumps({"test": test, "capability": capability_id}) + "\\n")\n'
        "    except OSError:\n"
        "        pass\n"
        "\n"
        "\n"
        "def refusal_for(capability_id: str) -> Optional[Dict[str, Any]]:\n"
        '    """The typed refusal for a declared capability, else None."""\n'
        "    connectors = PLACEHOLDER_CONNECTORS.get(capability_id) or []\n"
        "    if not connectors:\n"
        "        return None\n"
        "    _record(capability_id)\n"
        "    settings = [setting_for(c) for c in connectors]\n"
        "    return {\n"
        '        "ok": False,\n'
        '        "error_kind": UNAVAILABLE_KIND,\n'
        '        "capability": capability_id,\n'
        '        "connectors": list(connectors),\n'
        '        "settings": settings,\n'
        '        "error": "not configured: " + ", ".join(\n'
        '            "%s is a placeholder connector (needs %s)" % (c, s)\n'
        "            for c, s in zip(connectors, settings)\n"
        "        ),\n"
        "    }\n"
        "\n"
        "\n"
        "def is_declared_refusal(capability_id: str, status_code: int, body: Any) -> bool:\n"
        '    """True only for the typed refusal of a DECLARED capability.\n'
        "\n"
        "    A refusal from a capability that declared nothing is not this; it\n"
        '    stays whatever failure it is."""\n'
        "    connectors = PLACEHOLDER_CONNECTORS.get(capability_id) or []\n"
        "    return (\n"
        "        bool(connectors)\n"
        "        and status_code == UNAVAILABLE_STATUS\n"
        "        and isinstance(body, dict)\n"
        '        and body.get("ok") is False\n'
        '        and body.get("error_kind") == UNAVAILABLE_KIND\n'
        '        and list(body.get("settings") or []) == [setting_for(c) for c in connectors]\n'
        "    )\n"
    )


def render_contract_tests(blueprint: Any, samples: Dict[str, Dict[str, Any]]) -> str:
    """``tests/test_placeholder_contract.py``: the AUTHORITY on what a declared
    capability answers. Empty string when the blueprint declares none."""
    mapping = placeholder_connectors(blueprint)
    if not mapping:
        return ""
    lines = [
        '"""What a capability calling a DECLARED placeholder connector answers.',
        "",
        "Written by the Factory from the blueprint. This file is the authority:",
        "a test elsewhere that expects a live answer from these capabilities is",
        'a test defect, not a product failure."""',
        "",
        "import os",
        "",
        "from fastapi.testclient import TestClient",
        "",
        "from app.main import app",
        "",
        "client = TestClient(app)",
        'AUTH = {"Authorization": "Bearer " + os.environ["PLATFORM_TOKEN"]}',
    ]
    for cap_id, connectors in sorted(mapping.items()):
        name = cap_id.replace("-", "_")
        settings = [setting_for(c) for c in connectors]
        sample = dict(samples.get(cap_id) or {})
        lines += [
            "",
            "",
            f"def test_{name}_answers_the_declared_unavailable_refusal():",
            f'    before = client.get("/v1/{name}", headers=AUTH)',
            f'    resp = client.post("/v1/{name}", json={sample!r}, headers=AUTH)',
            f"    assert resp.status_code == {UNAVAILABLE_STATUS}, resp.text[:300]",
            "    body = resp.json()",
            '    assert body["ok"] is False',
            f'    assert body["error_kind"] == {UNAVAILABLE_KIND!r}',
            f'    assert body["settings"] == {settings!r}',
            "    # Refused before the handler: nothing was stored.",
            f'    after = client.get("/v1/{name}", headers=AUTH)',
            "    assert after.status_code == 200",
            "    assert after.json() == before.json()",
        ]
    return "\n".join(lines) + "\n"


def read_refusal_log(path: Any) -> Dict[str, List[str]]:
    """``{nodeid: [capability, ...]}`` from the product's refusal log -- typed
    records the product wrote while each test ran. Missing or unreadable
    means none were recorded."""
    import json
    from pathlib import Path

    out: Dict[str, List[str]] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, TypeError):
        return out
    for line in text.splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        test, cap = rec.get("test"), rec.get("capability")
        if isinstance(test, str) and test and isinstance(cap, str) and cap:
            caps = out.setdefault(test, [])
            if cap not in caps:
                caps.append(cap)
    return out


def defect_work_item(nodeid: str, capabilities: List[str]) -> str:
    """The work-list item the WRITER gets for one test defect."""
    return (
        f"{TEST_DEFECT}: {nodeid} -- this test expected a live answer from "
        f"{', '.join(capabilities)}, whose connectors are declared placeholders; "
        f"the product correctly answered HTTP {UNAVAILABLE_STATUS} error_kind "
        f'"{UNAVAILABLE_KIND}". Regenerate this test to assert that refusal '
        f"({CONTRACT_TEST} is the authority). Do not change the product for it."
    )
