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
        "once the connector exists. In your tests for these capabilities, "
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
        "from typing import Any, Dict, List, Optional\n"
        "\n"
        f"UNAVAILABLE_KIND = {UNAVAILABLE_KIND!r}\n"
        f"UNAVAILABLE_STATUS = {UNAVAILABLE_STATUS!r}\n"
        f"SETTING_SUFFIX = {SETTING_SUFFIX!r}\n"
        f"PLACEHOLDER_CONNECTORS: Dict[str, List[str]] = {mapping!r}\n"
        "\n"
        "\n"
        "def setting_for(connector_id: str) -> str:\n"
        "    return connector_id.upper() + SETTING_SUFFIX\n"
        "\n"
        "\n"
        "def refusal_for(capability_id: str) -> Optional[Dict[str, Any]]:\n"
        '    """The typed refusal for a declared capability, else None."""\n'
        "    connectors = PLACEHOLDER_CONNECTORS.get(capability_id) or []\n"
        "    if not connectors:\n"
        "        return None\n"
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
