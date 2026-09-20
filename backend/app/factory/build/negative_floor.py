"""The negative-case harness the Factory writes for every capability.

``negative_floor`` asks each capability for four counter-cases. Asking the
coder for them and grading it afterwards is the loop this whole exercise
exists to close: the agent improvises four assertions, the gate counts them,
and a rework round pays for the difference. The Factory knows the shape of
every counter-case from the capability's own spec -- which field is
required, which has a vocabulary, which type each one is -- so it writes the
harness and the agent extends it with the domain judgement only it has.

Four cases per capability, and they are the four the floor names:

  * a required field omitted            -> refused, not defaulted
  * a value outside a declared vocabulary (or, where the brief declares
    none, a value of the wrong type)    -> refused
  * another tenant's record read        -> 404, never 403 and never the row
  * a malformed payload                 -> refused, never a 500

Nothing here is domain-specific: every value is derived from the spec the
brief produced, so a bakery and an aviation desk get the same four questions
asked about their own fields.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

HEADER = '''"""Counter-cases: what each capability REFUSES.

Written by the factory TESTER role. The floor (negative_floor) requires four
per capability; these four are derived from each capability's own spec. Add
the domain cases the brief implies -- the boundary of a rule, a state machine
that must not skip a step -- they count toward the same floor.
"""

import os

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
AUTH = {"Authorization": "Bearer " + os.environ.get("PLATFORM_TOKEN", "dev-local-token")}
OTHER_TENANT = {
    "Authorization": "Bearer " + os.environ.get("PLATFORM_TOKEN_B", "dev-local-token-b")
}

REFUSED = (400, 403, 404, 409, 422)


def _refused(response):
    """A refusal, by status or by the envelope's own ok:false."""
    if response.status_code in REFUSED:
        return True
    if response.status_code != 200:
        return False
    try:
        body = response.json()
    except Exception:
        return False
    return isinstance(body, dict) and body.get("ok") is False
'''


def _required_field(spec: Mapping[str, Any]) -> Optional[str]:
    """A field the handler will miss. Prefer one the spec marks required."""
    fields = [f for f in (spec.get("fields") or []) if isinstance(f, dict) and f.get("name")]
    for field in fields:
        if field.get("required"):
            return str(field["name"])
    return str(fields[0]["name"]) if fields else None


def _vocabulary_field(spec: Mapping[str, Any]) -> Optional[str]:
    for field in spec.get("fields") or []:
        if isinstance(field, dict) and field.get("allowed_values") and field.get("name"):
            return str(field["name"])
    return None


def _typed_field(spec: Mapping[str, Any]) -> Optional[str]:
    """A non-string field, so a wrong TYPE is a real violation."""
    for field in spec.get("fields") or []:
        if not isinstance(field, dict) or not field.get("name"):
            continue
        if str(field.get("type") or "str").lower() in ("int", "float", "bool", "json"):
            return str(field["name"])
    return None


def render_negative_tests(
    specs: Mapping[str, Mapping[str, Any]],
    samples: Mapping[str, Mapping[str, Any]],
) -> str:
    """The counter-case suite for every capability in ``specs``."""
    lines: List[str] = [HEADER]
    caps = sorted(specs)
    if not caps:
        lines.append("\n\ndef test_no_capabilities():\n    pass\n")
        return "\n".join(lines)

    for cid in caps:
        spec = specs[cid] or {}
        name = str(cid).replace("-", "_")
        route = "/v1/%s" % name
        sample = dict(samples.get(cid) or {})
        required = _required_field(spec)
        vocab = _vocabulary_field(spec)
        typed = _typed_field(spec)

        lines.append("\n\n# -- %s %s" % (name, "-" * max(4, 60 - len(name))))

        if not required:
            # A spec with no declared fields still owes four counter-cases,
            # and an empty payload is the one question that needs no field
            # name to ask. Without this the capability scores 3 and fails
            # the floor for a reason its author cannot act on.
            lines += [
                "",
                "",
                "def test_%s_refuses_an_empty_payload():" % name,
                "    # Nothing at all is not a record.",
                '    resp = client.post("%s", json={}, headers=AUTH)' % route,
                "    assert resp.status_code in (400, 403, 404, 409, 422) or _refused(resp), (",
                '        "%s accepted an empty payload: " + resp.text[:200]' % name,
                "    )",
            ]

        if required:
            short = {k: v for k, v in sample.items() if k != required}
            lines += [
                "",
                "",
                "def test_%s_refuses_a_missing_required_field():" % name,
                '    """A partial record is refused, never completed by the handler."""',
                "    body = %r" % (short,),
                '    resp = client.post("%s", json=body, headers=AUTH)' % route,
                "    assert resp.status_code in (400, 403, 404, 409, 422) or _refused(resp), (",
                '        "%s accepted a payload with no %s: " + resp.text[:200]' % (name, required),
                "    )",
            ]

        if vocab:
            bad = dict(sample)
            bad[vocab] = "not-a-declared-value"
            lines += [
                "",
                "",
                "def test_%s_refuses_a_value_outside_its_vocabulary():" % name,
                '    """A column that accepts any string is not that column."""',
                "    body = %r" % (bad,),
                '    resp = client.post("%s", json=body, headers=AUTH)' % route,
                "    assert resp.status_code in (400, 403, 404, 409, 422) or _refused(resp), (",
                '        "%s accepted an undeclared %s: " + resp.text[:200]' % (name, vocab),
                "    )",
            ]
        elif typed:
            bad = dict(sample)
            bad[typed] = "not-the-declared-type"
            lines += [
                "",
                "",
                "def test_%s_refuses_a_value_of_the_wrong_type():" % name,
                '    """The brief declares no vocabulary here; the type is still a contract."""',
                "    body = %r" % (bad,),
                '    resp = client.post("%s", json=body, headers=AUTH)' % route,
                "    assert resp.status_code in (400, 403, 404, 409, 422) or _refused(resp), (",
                '        "%s accepted a wrongly typed %s: " + resp.text[:200]' % (name, typed),
                "    )",
            ]
        else:
            lines += [
                "",
                "",
                "def test_%s_refuses_an_unknown_field():" % name,
                '    """Nothing in the spec is typed or enumerated, so the surface',
                '    itself is the contract: an undeclared column is not silently kept."""',
                "    body = dict(%r)" % (sample,),
                '    body["not_a_declared_column"] = "x"',
                '    resp = client.post("%s", json=body, headers=AUTH)' % route,
                "    created = resp.status_code == 200",
                "    if created:",
                "        try:",
                "            echoed = resp.json()",
                "        except Exception:",
                "            echoed = {}",
                '        assert "not_a_declared_column" not in str(echoed), (',
                '            "%s stored a column it never declared"' % name,
                "        )",
            ]

        lines += [
            "",
            "",
            "def test_%s_does_not_leak_across_tenants():" % name,
            '    """404, never 403 and never the row: existence itself is private."""',
            "    body = %r" % (sample,),
            '    made = client.post("%s", json=body, headers=AUTH)' % route,
            "    if made.status_code != 200:",
            '        pytest.skip("capability did not accept the sample record")',
            "    try:",
            "        record = made.json()",
            "    except Exception:",
            '        pytest.skip("capability did not answer JSON")',
            '    rid = (record.get("id") or (record.get("result") or {}).get("id")',
            '           if isinstance(record, dict) else None)',
            "    if not rid:",
            '        pytest.skip("capability returned no record id to read back")',
            '    other = client.get("%s/" + str(rid), headers=OTHER_TENANT)' % route,
            "    assert other.status_code == 404, (",
            '        "%s answered %%s to another tenant, not 404" %% other.status_code' % name,
            "    )",
            "",
            "",
            "def test_%s_refuses_a_malformed_payload():" % name,
            '    """Garbage in is a refusal, never a 500 and never a stored row."""',
            "    for junk in ([], \"\", {\"\": None}, {\"x\" * 300: \"y\" * 2000}):",
            '        resp = client.post("%s", json=junk, headers=AUTH)' % route,
            "        assert resp.status_code != 500, (",
            '            "%s raised on malformed input: " + resp.text[:200]' % name,
            "        )",
            "        assert resp.status_code in (400, 403, 404, 409, 422) or _refused(resp), (",
            '            "%s accepted malformed input %%r" %% (junk,)' % name,
            "        )",
        ]
    return "\n".join(lines) + "\n"
