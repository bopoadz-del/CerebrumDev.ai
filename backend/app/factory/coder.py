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
import json
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

from .blueprint import CapabilitySpec, ProductBlueprint
from .build.authority import kernel_seat_brief

logger = logging.getLogger("cerebrumdev.factory.coder")

CODER_ENABLED_ENV = "FACTORY_CODER_ENABLED"

#: Provider-agnostic name for the agentic coding CLI. ``KIMI_CODE_CLI`` stays
#: honoured so existing deployments keep working unchanged; point
#: FACTORY_CODE_CLI at the Claude Code CLI to use Claude as the agentic coder.
#: The seam is "run this command, read its result" -- no CLI's internals are
#: depended on, and neither CLI's name is hardcoded at a call site.
CODE_CLI_ENV = "FACTORY_CODE_CLI"
LEGACY_CODE_CLI_ENV = "KIMI_CODE_CLI"


def code_cli_command(default: str = "kimi") -> str:
    """The agentic coder CLI to invoke. FACTORY_CODE_CLI wins, then legacy."""
    return os.getenv(CODE_CLI_ENV, "").strip() or os.getenv(
        LEGACY_CODE_CLI_ENV, ""
    ).strip() or default


class CoderError(RuntimeError):
    """The coder LLM could not produce a handler. Never fabricate instead."""


def coder_enabled() -> bool:
    return os.getenv(CODER_ENABLED_ENV, "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


CODER_BUDGET_ENV = "FACTORY_CODER_BUDGET_S"


def coder_budget_s() -> float:
    """Total wall-clock the coder may spend across one generation (seconds).

    Each GENERATE capability is up to two sequential 120 s LLM calls and the
    loop runs inside the HTTP request on a single-worker service, so an
    uncapped plan of N such capabilities holds a worker for N x 240 s. When
    the budget runs out, remaining capabilities ship the honest stub with the
    reason recorded — the same disclosure path as any other coder failure.
    0 disables the budget.
    """
    try:
        return float(os.getenv(CODER_BUDGET_ENV, "300"))
    except ValueError:
        return 300.0


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


#: Anthropic pins its API by date rather than by URL path.
ANTHROPIC_VERSION = "2023-06-01"

#: Completion budget for one artifact. 2048 was too tight for reasoning
#: models: they spend the budget on reasoning tokens and return
#: finish_reason="length" with empty content, which surfaced as a
#: CoderError and silently dropped that artifact to the template path
#: (observed once per five routes on a live kimi-k2.7-code build).
#: Override with FACTORY_CODER_MAX_TOKENS.
def _call_timeout_s() -> float:
    """Per-call ceiling for one coder request.

    Reasoning models legitimately take a minute or more, but an unbounded
    wait multiplied by retries and two model legs is how a build hangs.
    Override with FACTORY_CODER_TIMEOUT_S.
    """
    try:
        return max(30.0, float(os.getenv("FACTORY_CODER_TIMEOUT_S", "120")))
    except ValueError:
        return 120.0


def code_max_tokens() -> int:
    try:
        return max(256, int(os.getenv("FACTORY_CODER_MAX_TOKENS", "8192")))
    except ValueError:
        return 8192


MAX_CODE_TOKENS = code_max_tokens()


def _anthropic_request(cfg: Dict[str, Any], messages: List[Dict[str, str]], model: str):
    """Build the Messages API call. Deliberately not OpenAI-shaped.

    Three differences that silently break an OpenAI-style port:
    auth is ``x-api-key`` rather than a bearer token, the ``anthropic-version``
    header is mandatory, and the system prompt is a **top-level parameter** --
    a ``{"role": "system"}`` entry inside ``messages`` is rejected. The reply
    is a list of typed content blocks, not ``choices[0].message.content``.
    """
    system = "\n\n".join(
        m["content"] for m in messages if m.get("role") == "system"
    ).strip()
    turns = [m for m in messages if m.get("role") != "system"]

    headers = {
        "content-type": "application/json",
        "anthropic-version": ANTHROPIC_VERSION,
    }
    if cfg.get("api_key"):
        headers["x-api-key"] = cfg["api_key"]

    payload: Dict[str, Any] = {
        "model": model,
        "max_tokens": code_max_tokens(),
        "messages": turns,
    }
    temperature = cfg.get("temperature")
    if temperature is not None:
        payload["temperature"] = temperature
    if system:
        payload["system"] = system

    url = f"{cfg['base_url'].rstrip('/')}/messages"
    return url, payload, headers


def _anthropic_text(data: Dict[str, Any]) -> str:
    """Concatenate the text blocks of a Messages response.

    Content is a list of typed blocks; non-text blocks are skipped rather than
    stringified, so a future block type cannot leak JSON into generated code.
    """
    blocks = data.get("content") or []
    if not isinstance(blocks, list):
        raise ValueError("malformed Anthropic response: content is not a list")
    text = "".join(
        b.get("text", "")
        for b in blocks
        if isinstance(b, dict) and b.get("type") == "text"
    )
    if not text.strip():
        raise ValueError("empty completion")
    return text


def _llm_code_call(messages: List[Dict[str, str]]) -> str:
    """Text completion against the factory LLM. Raises CoderError on failure."""
    from .product_architect import get_factory_llm_config

    cfg = get_factory_llm_config()
    if cfg.get("mock"):
        raise CoderError("LLM mock mode — coder has no model to call")
    if cfg.get("error"):
        # Fail closed with the provider's own message; never borrow the other
        # provider's credentials to keep going.
        raise CoderError(str(cfg["error"]))
    provider = cfg.get("provider")
    if provider not in ("moonshot", "kimi", "claude"):
        raise CoderError(f"unsupported coder provider: {provider!r}")

    def _try(model: str) -> str:
        if provider == "claude":
            url, payload, headers = _anthropic_request(cfg, messages, model)
            resp = httpx.post(url, json=payload, headers=headers, timeout=120.0)
            resp.raise_for_status()
            return _anthropic_text(resp.json())

        headers = {"Content-Type": "application/json"}
        if cfg.get("api_key"):
            headers["Authorization"] = f"Bearer {cfg['api_key']}"
        url = f"{cfg['base_url'].rstrip('/')}/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": code_max_tokens(),
        }
        # Send a temperature ONLY when one was configured. This hardcoded 0.2
        # made the coder unusable on every reasoning model: kimi-k2.x and k3
        # answer 400 "invalid temperature: only 1 is allowed for this model",
        # so the factory silently fell back to templates on exactly the models
        # capable enough to write working code. llm_config._llm_temperature()
        # already returns None by default for this reason; the coder was the
        # one caller ignoring it.
        temperature = cfg.get("temperature")
        if temperature is not None:
            payload["temperature"] = temperature
        resp = httpx.post(url, json=payload, headers=headers, timeout=_call_timeout_s())
        resp.raise_for_status()
        data = resp.json()
        choice = data["choices"][0]
        content = choice["message"].get("content")
        if not content or not content.strip():
            # Say WHY it was empty. A bare "empty completion" costs a live
            # debugging run to interpret; finish_reason and usage identify the
            # cause for free the next time it happens. On a reasoning model the
            # usual answer is finish_reason="length" with the budget spent on
            # reasoning tokens before any content was emitted.
            usage = data.get("usage") or {}
            reasoning = choice["message"].get("reasoning_content") or ""
            raise ValueError(
                "empty completion from "
                + str(model)
                + f" (finish_reason={choice.get('finish_reason')!r}, "
                + f"max_tokens={MAX_CODE_TOKENS}, "
                + f"completion_tokens={usage.get('completion_tokens')}, "
                + f"reasoning_chars={len(reasoning)})"
            )
        return content

    def _try_with_connect_retries(model: str) -> str:
        """Transient transport failures get bounded retries with backoff.

        Two live campaign runs were lost to intermittent DNS: a single
        ``getaddrinfo failed`` at the wrong moment permanently degraded an
        artifact to the template path and the whole build failed honestly on
        it hours later. Only connection-class errors retry -- an HTTP status
        or an empty completion is the model answering, and retrying those
        would just spend money on the same answer.
        """
        import time as _time

        last: Exception | None = None
        for attempt in range(3):
            try:
                return _try(model)
            # ReadTimeout is deliberately NOT retried. It means the model did
            # not answer within the call timeout; a retry costs the same wait
            # again for the same likely outcome. Retrying it turned one slow
            # artifact into up to 18 minutes (3 attempts x 2 model legs x
            # 180s) and left a production build stuck in the WRITER for 39
            # minutes with no way to see why. Connection failures are the
            # genuinely transient case.
            except (httpx.ConnectError, httpx.ConnectTimeout,
                    httpx.RemoteProtocolError) as exc:
                last = exc
                if attempt < 2:
                    _time.sleep(2 * (attempt + 1))
        raise last

    try:
        return _try_with_connect_retries(cfg["model"])
    except Exception as first:  # noqa: BLE001 — one fallback, then honest failure
        fallback = cfg.get("fallback_model")
        if not fallback or fallback == cfg["model"]:
            raise CoderError(f"coder LLM failed: {type(first).__name__}: {first}") from first
        try:
            return _try_with_connect_retries(fallback)
        except Exception as second:  # noqa: BLE001
            # Carry both MESSAGES, not just the class names. This line used to
            # read "failed on primary (ValueError) and fallback
            # (HTTPStatusError)", which is unactionable: it cost a live
            # debugging run to discover the ValueError was an empty completion
            # from a starved token budget. coder_failures is often the only
            # record anyone sees, so it has to carry the reason.
            raise CoderError(
                f"coder LLM failed on primary {cfg['model']} "
                f"({type(first).__name__}: {first}) and fallback {fallback} "
                f"({type(second).__name__}: {second})"
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

    # The body must actually return from the function it lives in. Seen live:
    # the model wrapped its whole logic in a nested ``def endpoint(...)`` that
    # nothing calls, so the real function fell through to None and every route
    # answered ResponseValidationError. A return inside a nested function does
    # not count.
    outer = tree.body[0]
    todo = list(outer.body)
    has_return = False
    while todo:
        node = todo.pop()
        if isinstance(node, ast.Return):
            has_return = True
            break
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue  # a nested scope's return is not this function's return
        todo.extend(ast.iter_child_nodes(node))

    if not has_return:
        raise CoderError(
            f"coder output for {capability_id} never returns from the function "
            "body (a return inside a nested def does not count) -- the caller "
            "would receive None"
        )
    return indented


def _call_validate_retry(messages: List[Dict[str, str]], capability_id: str) -> str:
    """One LLM call, statically validated; ONE bounded retry on rejection.

    The tenth live build lost a whole capability -- and with it the run --
    because a single emitted body failed the never-returns check and the
    CoderError dropped it straight to the template. The model that wrote it
    could have fixed it in seconds if told; a full rework round to rediscover
    the same thing costs ~15 calls. Still "reject, never repair": the repair
    is another model call judged by the same gate, and a second rejection
    raises exactly as before.
    """
    raw = _llm_code_call(messages)
    try:
        return _validate_body(_strip_fences(raw), capability_id)
    except CoderError as exc:
        retry = messages + [
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": (
                    "Your code was rejected by a static gate:\n"
                    f"{exc}\n"
                    "Emit the corrected body now, following every rule in the "
                    "system message."
                ),
            },
        ]
        raw = _llm_code_call(retry)
        return _validate_body(_strip_fences(raw), capability_id)


_PLATFORM_SYSTEM = kernel_seat_brief("WRITER") + """

You write ONE Python function body for a generated business platform.

Contract:
- Emit ONLY the body of:  def handle(payload: dict) -> dict
- Module-level names already available to you:
    CAPABILITY_ID : str        the capability you are implementing
    BLOCK_IDS     : list[str]  vendored blocks you may call
    BLOCK_DEFAULT_ACTIONS : dict[str, str]  each block's default action
    execute(block_id: str, payload: dict, action: str | None = None,
            params: dict | None = None) -> dict
      Runs a vendored block LOCALLY, in-process. There is no network and no
      remote store; do not attempt HTTP, and do not import anything.
- Blocks are ACTION-DISPATCHED. Pass the action that fits the capability
  (the user message lists each block's contract: actions, declared inputs,
  and the input fields its schema REQUIRES). The dict you pass as `payload`
  becomes the block's input: build it so every required input field is
  present, mapped or derived from the caller's payload.
- The caller knows NOTHING about blocks. NEVER require a block-specific
  field (like "steps" or "channel") from the caller's payload -- CONSTRUCT
  it inside the handler from the domain data the capability does have.
  Validate only the capability's own fields.
- The platform runs OFFLINE and its suite BLOCKS outbound network. Never
  choose webhook URLs, SMTP/email, or Slack. For notification, the Store
  only accepts known channels: set channel to "mcp" and pass block=<a
  vendored block id from the roster> (tool with the same id). Do NOT set
  channel to "in_process" — the Store answers "Unknown channel: in_process".
- execute() never raises for a block-level failure; it returns an envelope.
  Treat result.get("status") == "error" (or an "error" key in the result)
  as a failure: surface it in your return value as {"ok": False,
  "error": ...} rather than pretending success.
- Use execute() for every block in BLOCK_IDS whose output the capability needs.
- Return a JSON-serialisable dict. Include "capability": CAPABILITY_ID.
- Standard library only, and no import statements at all. No network, no
  filesystem, no subprocess, no eval/exec.
- Validate inputs and return {"ok": False, "error": "..."} for bad requests
  rather than raising.
- Do NOT define a nested function and put your logic inside it — the
  enclosing function would fall through and return None. Return directly
  from the statements you write.
- No markdown fences, no def line, no commentary — statements only, written at
  column zero. They are indented into the function for you; do not add leading
  indentation yourself or the body will not compile.
"""


_SPEC_SYSTEM = kernel_seat_brief("WRITER") + """

You design the data model for one capability of a business platform.

Return ONLY a JSON object, no prose and no markdown fences:
{
  "entity": "<snake_case entity name, singular>",
  "fields": [
    {"name": "<snake_case>", "type": "str|int|float|bool", "required": true,
     "allowed_values": ["<one of a fixed set>", "..."],
     "min": <number>, "max": <number>}
  ]
}

Rules:
- 3 to 8 fields, ordered with identifiers first.
- Types are limited to str, int, float, bool. No nested objects, no lists.
- Do NOT include an "id" field; the platform adds its own primary key.
- Field names must be valid Python identifiers and must not shadow builtins.

Constraints are OPTIONAL, and they are the ONLY place a value restriction may
be expressed:
- "allowed_values": for a str field with a fixed vocabulary (a status, a
  severity, a category). 2 to 8 entries. Declare it whenever the field really
  does have a fixed set -- the platform enforces it and tests against it.
- "min" / "max": inclusive bounds for an int or float field.
Omit a constraint you do not mean. Anything you do NOT declare here cannot be
enforced later, so a restriction that matters must appear in this spec.
"""

_ALLOWED_FIELD_TYPES = {"str", "int", "float", "bool"}
#: Guard rails on agent-declared vocabularies. A field with one allowed value
#: is a constant, not an enum; an unbounded list is usually a hallucinated
#: free-text field.
_MIN_ALLOWED_VALUES = 2
_MAX_ALLOWED_VALUES = 8


def _clean_constraints(item: Dict[str, Any], ftype: str) -> Dict[str, Any]:
    """Validate the optional constraints on one field spec.

    Silently drops anything malformed rather than trusting it: a constraint
    the platform cannot enforce is worse than none, because the route would
    reject payloads the tests are entitled to send.
    """
    out: Dict[str, Any] = {}

    raw_allowed = item.get("allowed_values")
    if ftype == "str" and isinstance(raw_allowed, list):
        values, seen = [], set()
        for v in raw_allowed:
            if not isinstance(v, str):
                continue
            v = v.strip()
            if v and v not in seen:
                seen.add(v)
                values.append(v)
        if _MIN_ALLOWED_VALUES <= len(values) <= _MAX_ALLOWED_VALUES:
            out["allowed_values"] = values

    if ftype in ("int", "float"):
        lo, hi = item.get("min"), item.get("max")
        cast = int if ftype == "int" else float
        try:
            lo = cast(lo) if isinstance(lo, (int, float)) and not isinstance(lo, bool) else None
            hi = cast(hi) if isinstance(hi, (int, float)) and not isinstance(hi, bool) else None
        except (TypeError, ValueError):
            lo = hi = None
        # An inverted range would make every value invalid.
        if lo is not None and hi is not None and lo > hi:
            lo = hi = None
        if lo is not None:
            out["min"] = lo
        if hi is not None:
            out["max"] = hi

    return out


def generate_model_spec(
    *, capability_id: str, description: str, product_name: str, vertical: str
) -> Dict[str, Any]:
    """Ask the coder to *design* a schema, returned as validated JSON.

    Structured artifacts go through a spec rather than raw Python. The model
    is the one thing every other artifact is derived from -- persistence
    columns, route payloads, tests -- so a hallucinated import or a subtly
    broken class definition would propagate into four files instead of one.
    Asking for JSON and rendering it deterministically keeps the *design*
    with the agent and the *structure* guaranteed.
    """
    import json as _json

    user = (
        f"Platform: {product_name} (vertical: {vertical})\n"
        f"Capability id: {capability_id}\n"
        f"Capability description: {description}\n\n"
        "Design the entity this capability stores. Return the JSON now."
    )
    raw = _llm_code_call(
        [
            {"role": "system", "content": _SPEC_SYSTEM},
            {"role": "user", "content": user},
        ]
    )
    text = _strip_fences(raw).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise CoderError(f"model spec for {capability_id} is not JSON")
    try:
        data = _json.loads(text[start : end + 1])
    except ValueError as exc:
        raise CoderError(f"model spec for {capability_id} is not valid JSON: {exc}") from exc

    entity = str(data.get("entity") or "").strip()
    if not entity.isidentifier():
        raise CoderError(f"model spec for {capability_id} has a bad entity name: {entity!r}")

    fields = []
    for item in data.get("fields") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        ftype = str(item.get("type") or "str").strip()
        if not name.isidentifier() or name in {"id", "self"} or name.startswith("_"):
            continue
        if ftype not in _ALLOWED_FIELD_TYPES:
            continue
        fields.append(
            {
                "name": name,
                "type": ftype,
                "required": bool(item.get("required", True)),
                **_clean_constraints(item, ftype),
            }
        )

    # Dedupe, keeping first occurrence, and cap the width.
    seen, unique = set(), []
    for f in fields:
        if f["name"] in seen:
            continue
        seen.add(f["name"])
        unique.append(f)
    if not unique:
        raise CoderError(f"model spec for {capability_id} declared no usable fields")

    model = get_factory_llm_config_model()
    logger.info("coder designed %s.%s (%d fields)", capability_id, entity, len(unique))
    return {"entity": entity, "fields": unique[:8], "model": model}


def get_factory_llm_config_model() -> str:
    from .product_architect import get_factory_llm_config

    return get_factory_llm_config().get("model", "unknown")


_ROUTE_SYSTEM = kernel_seat_brief("WRITER") + """

You write ONE Python function body for an API route in a generated platform.

The kernel already templates GET list and GET-by-id, plus the job routes
(GET /v1/jobs, /catalog, /inventory, /capabilities, /gates, /provenance).
You only write the POST create body.

Contract:
- Emit ONLY the body of:  def endpoint(payload: dict) -> dict
- Module-level names already available to you:
    CAPABILITY_ID : str
    handle(payload: dict) -> dict    the capability handler
    save(record: dict) -> dict       persists and returns the stored record
    list_all() -> list[dict]         every stored record
- Typical shape: validate the payload, call handle(), persist the result with
  save(), and return a dict describing what happened.
- Return a JSON-serialisable dict. Return {"ok": False, "error": "..."} for a
  bad request rather than raising.

VALIDATION IS BOUNDED BY THE SPEC. You are given each field's type and, where
one exists, its allowed_values or min/max. Enforce presence, the declared
type, and those declared constraints -- and nothing else. Do NOT invent a
vocabulary, a format, a regex or a range that the spec does not state. The
platform's tests build their payloads from this same spec, so a restriction
you invent will reject a request that is valid by contract, and the build
fails with no way for either side to give way.
- Standard library only, and no import statements at all. No network, no
  filesystem, no subprocess, no eval/exec.
- Do NOT define a nested function and put your logic inside it — the
  enclosing function would fall through and return None. Return directly
  from the statements you write.
- No markdown fences, no def line, no commentary — statements only, written at
  column zero. They are indented for you; do not add leading indentation.
"""


def describe_fields(fields: List[Dict[str, Any]]) -> str:
    """Render the field contract the route must validate against, and only that."""
    lines = []
    for f in fields:
        bits = [f"{f['name']}: {f['type']}"]
        if f.get("allowed_values"):
            bits.append("one of " + ", ".join(repr(v) for v in f["allowed_values"]))
        if f.get("min") is not None:
            bits.append(f"min {f['min']}")
        if f.get("max") is not None:
            bits.append(f"max {f['max']}")
        if not f.get("required", True):
            bits.append("optional")
        lines.append("  - " + " | ".join(bits))
    return "\n".join(lines)


def generate_route_body(
    *,
    capability_id: str,
    description: str,
    entity: str,
    fields: List[Dict[str, Any]],
    work_list: Optional[List[str]] = None,
    previous_attempt: Optional[str] = None,
) -> Dict[str, Any]:
    """Write the endpoint body for one capability's route.

    Takes the full field specs, not just names: the route may enforce the
    declared constraints and nothing beyond them, so it has to be told what
    those constraints are.
    """
    lines = [
        f"Capability id: {capability_id}",
        f"Capability description: {description}",
        f"Entity being stored: {entity}",
        "Fields, with every constraint you are permitted to enforce:",
        describe_fields(fields),
    ]
    if work_list:
        lines.append(
            "\nA previous attempt failed these checks — fix them:\n"
            + "\n".join(f"- {item}" for item in work_list)
        )
    if previous_attempt:
        lines.append(
            "\nYOUR PREVIOUS ATTEMPT is below. It produced the failures "
            "above. Do not start over: keep what works and change only what "
            "the findings demand.\n----\n" + previous_attempt + "\n----"
        )
    lines.append("\nWrite the endpoint() body now.")

    body = _call_validate_retry(
        [
            {"role": "system", "content": _ROUTE_SYSTEM},
            {"role": "user", "content": "\n".join(lines)},
        ],
        f"{capability_id}:route",
    )
    model = get_factory_llm_config_model()
    logger.info(
        "coder wrote platform route %s (%d lines) via %s",
        capability_id,
        body.count("\n") + 1,
        model,
    )
    return {"body": body, "model": model}


def generate_platform_handler(
    *,
    capability_id: str,
    description: str,
    block_ids: List[str],
    product_name: str,
    vertical: str,
    work_list: Optional[List[str]] = None,
    block_contracts: Optional[Dict[str, Any]] = None,
    model_fields: Optional[List[Dict[str, Any]]] = None,
    previous_attempt: Optional[str] = None,
    vendored_roster: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Write the ``handle`` body for one runner-built capability.

    Distinct from :func:`generate_handler_body`, which serves the legacy
    template generator: that contract is a sandboxed in-memory stub with no
    block access, whereas this one is allowed to drive vendored blocks through
    the local dispatch runtime. Same validation gate applies to both.

    ``work_list`` carries the TESTER's findings on a rework pass so the coder
    is told what failed rather than guessing from scratch. ``block_contracts``
    carries what each block actually accepts (actions, declared inputs, schema
    required fields) -- without it the coder guesses payload shapes and real
    blocks reject them with "Input validation failed".
    """
    from .product_architect import get_factory_llm_config

    lines = [
        f"Platform: {product_name} (vertical: {vertical})",
        f"Capability id: {capability_id}",
        f"Capability description: {description}",
        f"BLOCK_IDS available to execute(): {block_ids!r}",
    ]
    if model_fields:
        lines.append(
            "\nThe payload arriving at handle() carries the capability's data "
            "model. Validate ONLY these fields; never demand any other:\n"
            + describe_fields(model_fields)
        )
    if block_contracts:
        lines.append(
            "\nBlock contracts (invoke each block with an action it supports "
            "and an input dict carrying every required field, CONSTRUCTED "
            "from the payload fields above -- the caller sends only those):\n"
            + json.dumps(block_contracts, indent=2, sort_keys=True)
        )
    if vendored_roster:
        lines.append(
            "\nEvery block vendored into this platform (a pipeline or "
            "orchestrator block's steps may reference any of these by id; "
            "a step must never reference the pipeline block itself): "
            + ", ".join(vendored_roster)
        )
    if work_list:
        lines.append(
            "\nA previous attempt failed these checks — fix them:\n"
            + "\n".join(f"- {item}" for item in work_list)
        )
    if previous_attempt:
        lines.append(
            "\nYOUR PREVIOUS ATTEMPT is below. It produced the failures "
            "above. Do not start over: keep what works and change only what "
            "the findings demand. If a block action rejected your input, "
            "either supply the fields its error names (derived from the "
            "payload) or pick a different action from that block's "
            "action_options that matches the capability's intent.\n"
            "----\n" + previous_attempt + "\n----"
        )
    lines.append("\nWrite the handle() body now.")

    body = _call_validate_retry(
        [
            {"role": "system", "content": _PLATFORM_SYSTEM},
            {"role": "user", "content": "\n".join(lines)},
        ],
        capability_id,
    )
    model = get_factory_llm_config().get("model", "unknown")
    logger.info(
        "coder wrote platform handler %s (%d lines) via %s",
        capability_id,
        body.count("\n") + 1,
        model,
    )
    return {"body": body, "model": model}


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


# -- Kernel-side agent (COLLECTOR review, TESTER extra cases) --------------
#
# The coding agent lives in WRITER for manufacturing. COLLECTOR and TESTER
# may *consult* it under their own authority: report-only binding review
# (COLLECTOR writes nothing) and extra domain cases that cannot replace
# kernel tests (TESTER writes only tests/). CLONER and STORE_MANAGER stay
# mechanical. See docs/factory/AGENT_IN_THE_KERNELS.md.

_COLLECTOR_REVIEW_SYSTEM = kernel_seat_brief("COLLECTOR") + """

The collector kernel already resolved block ids. You do NOT pick blocks and you do \
NOT change the plan. Review each capability↔block binding against the block \
contracts and return JSON only:

{"reviews": [{"capability_id": "...", "block_ids": ["..."], "verdict": "endorse" or "mismatch", "reason": "short"}]}

endorse = the blocks can plausibly serve the capability.
mismatch = strained fit an engineer would flag at tender review.
Never invent block ids. Never omit a capability you were given.
"""

_TESTER_CASES_SYSTEM = kernel_seat_brief("TESTER") + """

The tester kernel already wrote shape/persistence/dispatch tests. Propose ADDITIONAL \
domain cases as mutations of the sample payloads you are given — same keys, \
at least one value changed, no new keys. Return JSON only:

{"cases": [{"capability_id": "...", "payload": {}, "expect": "accept" or "reject", "reason": "short"}]}

expect=reject means the mutated payload should be refused.
expect=accept means it should still succeed.
Do not replace kernel tests. Do not invent capability ids or payload keys.
Do not ask to run tests over HTTP; GET /v1/gates describes coverage only.
"""


def _llm_json_object(messages: List[Dict[str, str]], what: str) -> Dict[str, Any]:
    raw = _llm_code_call(messages)
    text = _strip_fences(raw).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise CoderError(f"{what} is not JSON")
    try:
        data = json.loads(text[start : end + 1])
    except ValueError as exc:
        raise CoderError(f"{what} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CoderError(f"{what} is not a JSON object")
    return data


def review_capability_bindings(
    *,
    product_name: str,
    vertical: str,
    capabilities: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """COLLECTOR consults the coding agent. Report-only; raises CoderError."""
    user = (
        f"Platform: {product_name} (vertical: {vertical})\n"
        "Capability bindings and harvested contracts:\n"
        + json.dumps(capabilities, indent=2, sort_keys=True)
        + "\n\nReturn the reviews JSON now."
    )
    data = _llm_json_object(
        [
            {"role": "system", "content": _COLLECTOR_REVIEW_SYSTEM},
            {"role": "user", "content": user},
        ],
        "collector binding review",
    )
    reviews: List[Dict[str, Any]] = []
    known = {str(c.get("id") or "") for c in capabilities}
    for item in data.get("reviews") or []:
        if not isinstance(item, dict):
            continue
        cap_id = str(item.get("capability_id") or "").strip()
        if cap_id not in known:
            continue
        verdict = str(item.get("verdict") or "").strip().lower()
        if verdict not in {"endorse", "mismatch"}:
            continue
        reviews.append(
            {
                "capability_id": cap_id,
                "block_ids": [
                    str(b) for b in (item.get("block_ids") or []) if str(b).strip()
                ],
                "verdict": verdict,
                "reason": str(item.get("reason") or "")[:300],
            }
        )
    if not reviews:
        raise CoderError("collector binding review named no known capabilities")
    model = get_factory_llm_config_model()
    logger.info(
        "coder reviewed %d/%d collector bindings via %s",
        len(reviews),
        len(known),
        model,
    )
    return {"reviews": reviews, "model": model}


def propose_domain_test_cases(
    *,
    product_name: str,
    vertical: str,
    capabilities: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """TESTER consults the coding agent for extra domain cases. Raises CoderError."""
    user = (
        f"Platform: {product_name} (vertical: {vertical})\n"
        "Kernel sample payloads (mutate these, do not replace them):\n"
        + json.dumps(capabilities, indent=2, sort_keys=True)
        + "\n\nReturn the extra cases JSON now."
    )
    data = _llm_json_object(
        [
            {"role": "system", "content": _TESTER_CASES_SYSTEM},
            {"role": "user", "content": user},
        ],
        "tester domain cases",
    )
    known = {str(c.get("id") or "") for c in capabilities}
    cases: List[Dict[str, Any]] = []
    for item in data.get("cases") or []:
        if not isinstance(item, dict):
            continue
        cap_id = str(item.get("capability_id") or "").strip()
        if cap_id not in known:
            continue
        payload = item.get("payload")
        if not isinstance(payload, dict):
            continue
        expect = str(item.get("expect") or "").strip().lower()
        if expect not in {"accept", "reject"}:
            continue
        cases.append(
            {
                "capability_id": cap_id,
                "payload": payload,
                "expect": expect,
                "reason": str(item.get("reason") or "")[:300],
            }
        )
    if not cases:
        raise CoderError("tester proposed no usable domain cases")
    model = get_factory_llm_config_model()
    logger.info("coder proposed %d tester domain case(s) via %s", len(cases), model)
    return {"cases": cases, "model": model}
