"""The per-package deploy token. One helper, so no emitter invents its own.

F1 (owner's pilot test, 2026-09-29): every generated product's deploy env
carried the literal ``PLATFORM_TOKEN=dev-local-token`` — a world-known value —
so production accepted it unless the operator overrode it, and the writer had
a known token to hardwire a second-tenant backdoor against. The deploy env now
gets a value generated fresh for each package: knowing one product's token
tells an attacker nothing about any other, and nothing about a product whose
zip they have not been given.

The TEST bootstrap (tests/conftest.py) keeps the well-known dev values — that
is the one context they belong to, and the ``no_token_literal`` floor check
holds runtime ``app/**`` to it.
"""

from __future__ import annotations

import secrets


def deploy_platform_token() -> str:
    """A fresh, URL-safe bearer token for one package's deploy env."""
    return "pt-" + secrets.token_urlsafe(30)
