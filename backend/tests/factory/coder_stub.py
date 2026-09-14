"""Deterministic coder stubs — no paid calls, no network.

A stub-coder build records measured authorship (the stubbed agent authors
the README), so the WRITER gate's ``writer_no_output`` check passes
honestly: the build is a simulation of a coding-agent build, not a
template-only run.
"""

from __future__ import annotations

from typing import List
from unittest import mock

STUB_ROUTE = (
    "    result = handle(payload)\n"
    '    return {"ok": True, "capability": CAPABILITY_ID, "result": result}'
)

#: Keep the strings the runner tests assert on.
STUB_README = (
    "# stub readme — written by the stubbed coding agent\n\n"
    "GET /v1/jobs\n\n"
    "## Run it\n\n"
    "Install runtime deps from requirements.txt, then dev deps from "
    "requirements-dev.txt before running pytest.\n\n"
    "Binding surveyor\n"
)


def apply_stub_coder() -> List[mock._patch]:
    """Patch every coder entry point deterministically.

    Returns the active patches; the caller must ``stop()`` them. Suitable
    for module-scoped fixtures where pytest's ``monkeypatch`` has not run
    yet.
    """
    patches = [
        mock.patch(
            "app.factory.build.coder_session.cli_available",
            lambda command=None: False,
        ),
        mock.patch(
            "app.factory.coder.generate_model_spec",
            lambda **kw: {
                "entity": kw["capability_id"].replace("-", "_"),
                "fields": [{"name": "reference", "type": "str", "required": True}],
                "model": "stub-spec",
            },
        ),
        mock.patch(
            "app.factory.coder.generate_route_body",
            lambda **kw: {"body": STUB_ROUTE, "model": "stub-route"},
        ),
        mock.patch(
            "app.factory.coder._llm_code_call",
            lambda messages: (STUB_README, "stub-model"),
        ),
        mock.patch(
            "app.factory.coder.review_capability_bindings",
            lambda **kw: {
                "reviews": [
                    {
                        "capability_id": c.get("id"),
                        "block_ids": c.get("block_ids") or [],
                        "verdict": "endorse",
                        "reason": "stub",
                    }
                    for c in kw.get("capabilities") or []
                ],
                "model": "stub-collector",
            },
        ),
        mock.patch(
            "app.factory.coder.propose_domain_test_cases",
            lambda **kw: {"cases": [], "model": "stub-tester"},
        ),
    ]
    for patch in patches:
        patch.start()
    return patches
