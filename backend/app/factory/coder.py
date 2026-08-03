"""LLM coder for GENERATE-strategy capabilities.

The generator composes REUSE capabilities from prebuilt blocks without any
LLM. GENERATE capabilities are the opposite promise — logic no block
provides — and until now they shipped as honest ``dependency_required``
stubs. This module writes the missing handler bodies with the factory LLM
(same provider config as the Product Architect).

Failure policy — decided after the 2026-08-02 field test where dead LLM
credit made every path silently degrade:

* The coder RAISES ``CoderError`` on any LLM failure. It never fabricates.
* The generator catches it, ships the honest stub instead, and records the
  failure in the generation result so the chat/UI can say "N capabilities
  could not be coder-written (reason)" — degraded output is acceptable,
  invisible degradation is not.
* ``FACTORY_CODER_ENABLED=0`` skips the LLM entirely (stubs, recorded as
  "coder disabled").

Emitted code is stamped into the export with provenance (model, capability)
and wrapped by the kernel handler in try/except — a broken generated body
answers ``execution_error``, it does not crash the product.
"""

from __future__ import annotations

import ast
import logging
import os
from typing import Any, Dict, List

import httpx

from .blueprint import CapabilitySpec, ProductBlueprint

logger = logging.getLogger("cerebrumdev.factory.coder")

CODER_ENABLED_ENV = "FACTORY_CODER_ENABLED"


class CoderError(RuntimeError):
    """The coder LLM could not produce a handler. Never fabricate instead."""


def coder_enabled() -> bool:
    return os.getenv(CODER_ENABLED_ENV, "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


_SYSTEM = """You write ONE Python function body for a generated business platform.

Contract:
- You are given a capability description and the platform blueprint summary.
- Emit ONLY Python code for the body of:
    async def generated_logic(context: dict, arguments: dict) -> dict
- The body must return a JSON-serialisable dict describing the outcome of the
  requested operation. Use arguments.get("action") to dispatch operations the
  capability implies (list/create/update style), and keep state in the
  module-level ``_STATE`` dict (an in-memory store the wrapper provides).
- Standard library only. No imports of third-party packages. No network, no
  filesystem, no subprocess, no eval/exec.
- No markdown fences, no surrounding function definition, no commentary —
  raw indented statements only (they will be placed inside the function).
- Validate inputs and return {"ok": False, "error": "..."} dicts for bad
  requests rather than raising.
"""

# Dangerous stdlib modules the generated body may not import (directly or via
# ``from X import ...`` / ``import X.Y`` / ``import X as Z``). Safe stdlib
# (math, json, datetime, re, ...) is allowed.
_FORBIDDEN_MODULES = frozenset({
    "os", "sys", "subprocess", "socket", "shutil", "importlib", "ctypes",
    "pickle", "marshal", "builtins", "multiprocessing", "threading", "pty",
    "posix", "nt", "resource", "mmap", "fcntl", "signal", "code", "codeop",
    "gc", "inspect", "platform", "pathlib", "tempfile", "glob", "urllib",
    "http", "ftplib", "smtplib", "asyncio",
})
# Builtins that read/write the host or escape the sandbox.
_FORBIDDEN_NAMES = frozenset({
    "eval", "exec", "open", "__import__", "compile", "globals", "locals",
    "vars", "getattr", "setattr", "delattr", "input", "breakpoint",
    "__builtins__", "memoryview", "exit", "quit",
})
# Dunder attributes used for classic sandbox escapes
# (``().__class__.__bases__[0].__subclasses__()`` and friends).
_FORBIDDEN_ATTRS = frozenset({
    "__globals__", "__subclasses__", "__bases__", "__mro__", "__class__",
    "__code__", "__builtins__", "__dict__", "__import__", "__getattribute__",
    "__reduce__", "__reduce_ex__", "__base__", "__closure__",
})


class _ForbiddenNodeVisitor(ast.NodeVisitor):
    """Collect sandbox-escape constructs. AST-based, so it is not fooled by
    ``from os import environ``, ``import  os`` (odd spacing), aliasing, or
    ``getattr(__builtins__, "o"+"pen")`` that a substring scan misses."""

    def __init__(self) -> None:
        self.found: List[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            root = alias.name.split(".", 1)[0]
            if root in _FORBIDDEN_MODULES:
                self.found.append(f"import {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        root = (node.module or "").split(".", 1)[0]
        if root in _FORBIDDEN_MODULES:
            self.found.append(f"from {node.module} import ...")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in _FORBIDDEN_NAMES:
            self.found.append(f"name {node.id!r}")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in _FORBIDDEN_ATTRS:
            self.found.append(f"attribute {node.attr!r}")
        self.generic_visit(node)


def _llm_code_call(messages: List[Dict[str, str]]) -> str:
    """Text completion against the factory LLM. Raises CoderError on failure."""
    from .product_architect import get_factory_llm_config

    cfg = get_factory_llm_config()
    if cfg.get("mock"):
        raise CoderError("LLM mock mode — coder has no model to call")
    provider = cfg.get("provider")
    if provider not in ("moonshot", "kimi"):
        raise CoderError(f"unsupported coder provider: {provider!r}")

    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    url = f"{cfg['base_url'].rstrip('/')}/chat/completions"

    def _try(model: str) -> str:
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 2048,
        }
        resp = httpx.post(url, json=payload, headers=headers, timeout=120.0)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        if not content or not content.strip():
            raise ValueError("empty completion")
        return content

    try:
        return _try(cfg["model"])
    except Exception as first:  # noqa: BLE001 — one fallback, then honest failure
        fallback = cfg.get("fallback_model")
        if not fallback or fallback == cfg["model"]:
            raise CoderError(f"coder LLM failed: {type(first).__name__}: {first}") from first
        try:
            return _try(fallback)
        except Exception as second:  # noqa: BLE001
            raise CoderError(
                f"coder LLM failed on primary ({type(first).__name__}) and "
                f"fallback ({type(second).__name__})"
            ) from second


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
    if text.rstrip().endswith("```"):
        text = text.rstrip()[: text.rstrip().rfind("```")]
    return text.strip("\n")


def _validate_body(body: str, capability_id: str) -> str:
    """Static gate on emitted code. Reject, never repair.

    The gate parses the emitted body inside the exact wrapper it will live in
    and walks the AST for sandbox-escape constructs (dangerous imports,
    builtins, dunder attribute access). An AST walk — not a substring scan —
    is required: ``from os import environ``, aliased imports, odd spacing, and
    ``getattr(__builtins__, "o"+"pen")`` all slip past substrings.
    """
    if not body.strip():
        raise CoderError(f"coder returned an empty body for {capability_id}")
    # Must compile inside the wrapper shape it will actually live in.
    indented = "\n".join(
        ("    " + line) if line.strip() else line for line in body.splitlines()
    )
    wrapper = (
        "async def generated_logic(context, arguments):\n" + indented + "\n"
    )
    try:
        tree = compile(
            wrapper, f"<coder:{capability_id}>", "exec", flags=ast.PyCF_ONLY_AST
        )
    except SyntaxError as exc:
        raise CoderError(
            f"coder output for {capability_id} does not compile: {exc}"
        ) from exc

    visitor = _ForbiddenNodeVisitor()
    visitor.visit(tree)
    if visitor.found:
        raise CoderError(
            f"coder output for {capability_id} contains forbidden construct(s): "
            f"{', '.join(sorted(set(visitor.found)))}"
        )
    return indented


def generate_handler_body(cap: CapabilitySpec, blueprint: ProductBlueprint) -> Dict[str, Any]:
    """Write the handler body for one GENERATE capability.

    Returns {"body": <indented code>, "model": <model id>}. Raises CoderError
    on any failure — the generator decides what an honest fallback looks like.
    """
    from .product_architect import get_factory_llm_config

    user = (
        f"Platform: {blueprint.product_name} (vertical: {blueprint.vertical})\n"
        f"Platform summary: {blueprint.summary}\n\n"
        f"Capability id: {cap.id}\n"
        f"Capability description: {cap.description}\n\n"
        "Write the generated_logic body now."
    )
    raw = _llm_code_call(
        [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user},
        ]
    )
    body = _validate_body(_strip_fences(raw), cap.id)
    model = get_factory_llm_config().get("model", "unknown")
    logger.info("coder wrote %s (%d lines) via %s", cap.id, body.count("\n") + 1, model)
    return {"body": body, "model": model}
