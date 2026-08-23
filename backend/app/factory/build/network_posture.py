"""Factory network posture — exactly one, applied at emission time.

S7: .env.example and tests/conftest.py already claim offline. Capture's
vendored block.json defaulted llm_provider to deepseek (cloud) while
declaring permissions.network: false. That is permissions-vs-behaviour
(S1 F22; the S7 brief called it F21).

Chosen: P1 (offline strict).
Rejected: P2 local Ollama, P3 cloud allowlist. See REJECTED_ALTERNATIVES.

Do not invent a chat block. U11/ui_schema_builder stays unused because
Cerebrum-Blocks is not cloned.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple

# Exactly one. Do not add a second.
NETWORK_POSTURE = "P1"
POSTURE_ID = "P1"
NETWORK_POSTURE_REASON = (
    "Delivered platforms run in-process against vendored blocks; "
    "local/scripted OCR only; no Store URL, no cloud LLM, no Ollama, "
    "no outbound HTTP at runtime."
)

REJECTED_ALTERNATIVES: Dict[str, str] = {
    "P2": (
        "Local inference (Ollama :11434, 'no external call', loopback only). "
        "Would rewrite the socket blocker and retract 'no network' to "
        "'loopback LLM'. Ollama is not in the generated Dockerfile or Render "
        "blueprint. Factory products already promise no outbound call. "
        "Cost: new runtime dependency, new deploy surface, blocker change."
    ),
    "P3": (
        "Cloud allowlist; retract offline claim; state PII egress. "
        "Deleting the socket blocker is an S7 FAIL. Production generated "
        "products do not already require cloud capture or chat (F17 is "
        "capture listed not executed). Factory host LLM is the builder, "
        "not the product. Cost: every offline claim becomes a lie unless "
        "rewritten; PII egress is un-drilled."
    ),
}

P1_SOCKET_BLOCKER_MARKERS = (
    "offline suite: outbound connection",
    '_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}',
)

POSTURE_DOC = Path("docs") / "network_posture.json"

DELIVERY_ARTIFACTS: Tuple[str, ...] = (
    "Dockerfile",
    "README.md",
    ".env.example",
    "render.yaml",
    "app/main.py",
    "requirements.txt",
    "docs/network_posture.json",
    "docs/build_provenance.json",
)

P1_FORBIDDEN: Tuple[str, ...] = (
    "CEREBRUM_API_URL",
    "CEREBRUM_API_KEY",
    "/v1/execute",
)

DECLARED_GENERATOR_POSTURE_EXCEPTIONS: Tuple[str, ...] = (
    "ProductGenerator._write_env_example documents CEREBRUM_API_URL "
    "(template-path handlers POST to the store; S6 leftover)",
    "ProductGenerator._write_runtime_packaging may emit estate Postgres / FastEmbed",
)

P1_ENV_EXAMPLE = """# Copy to .env and fill in. Never commit real values.
ENV=production

# Where the sqlite file lives. Mount a volume at this path to persist.
STORAGE_PATH=./data

# P1: offline strict. Local/scripted OCR only. No LLM provider. No Ollama.
# No store URL. No store key. Vendored blocks, in-process dispatch.
# tests/conftest.py refuses non-loopback sockets. That blocker is unchanged.
"""

P1_CAPTURE_ADAPTER = '''"""P1 capture adapter. Factory CLONER emission.

Local OCR when tesseract is on PATH; otherwise scripted extraction from
provided text. No cloud LLM. No Ollama. No outbound HTTP.

Replaces the Store shim in the product workspace so a delivered platform
cannot inherit cloud-LLM defaults while block.json says network:false.
The Factory vendor-mirror Store-shim snapshot is unchanged. Blocks was not written.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict

POSTURE = "P1"


def _local_ocr(path: Path) -> str:
    exe = shutil.which("tesseract")
    if not exe or not path.is_file():
        return ""
    try:
        proc = subprocess.run(
            [exe, str(path), "stdout", "-l", "eng"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (proc.stdout or "").strip()


def _scripted_structure(raw: str) -> Dict[str, Any]:
    emails = re.findall(r"[\\w.+-]+@[\\w-]+\\.[\\w.-]+", raw)
    urls = re.findall(r"https?://\\S+", raw)
    numbers = re.findall(r"\\b\\d+(?:\\.\\d+)?\\b", raw)
    summary = raw[:240] + ("…" if len(raw) > 240 else "")
    return {
        "entities": {
            "emails": emails,
            "urls": urls,
            "numbers": numbers[:20],
        },
        "tags": ["p1", "scripted"],
        "summary": summary,
        "clean_text": " ".join(raw.split()),
    }


def _extract_text(data: Dict[str, Any]) -> tuple[str, str]:
    for key in ("raw_text", "text", "content", "body"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip(), "scripted"
    for key in ("path", "file", "image", "input"):
        value = data.get(key)
        if isinstance(value, (str, Path)) and Path(value).is_file():
            ocr = _local_ocr(Path(value))
            if ocr:
                return ocr, "tesseract"
            return "", "tesseract_unavailable"
    return "", "scripted"


def run(**kwargs: Any) -> Dict[str, Any]:
    data = kwargs.get("input", kwargs)
    if not isinstance(data, dict):
        data = {"input": data}
    raw, engine = _extract_text(data)
    structured = _scripted_structure(raw)
    digest = hashlib.sha256((raw or "empty").encode("utf-8")).hexdigest()[:16]
    return {
        "posture": POSTURE,
        "capture_id": digest,
        "raw_text": raw,
        "ocr_engine": engine,
        "llm_provider": "none",
        **structured,
    }
'''


class PostureError(ValueError):
    """Artifacts disagree with the chosen network posture."""


def declaration() -> Dict[str, Any]:
    return {
        "schema_version": "network_posture.v1",
        "posture": NETWORK_POSTURE,
        "reason": NETWORK_POSTURE_REASON,
        "rejected": REJECTED_ALTERNATIVES,
        "socket_blocker": "unchanged_non_loopback_refuse",
    }


def declaration_json() -> str:
    return json.dumps(declaration(), indent=2, sort_keys=True) + "\n"


def banner() -> str:
    return f"{NETWORK_POSTURE}: {NETWORK_POSTURE_REASON}"


def readme_section() -> str:
    return (
        "\n"
        "## Network posture\n"
        "\n"
        f"`{NETWORK_POSTURE}` — {NETWORK_POSTURE_REASON}\n"
        "\n"
        "This value is factory-stamped. A coding-agent README cannot change it.\n"
    )


def apply_p1_capture_manifest(data: Mapping[str, Any]) -> Dict[str, Any]:
    out = json.loads(json.dumps(data))
    out.setdefault("permissions", {})["network"] = False
    desc = str(out.get("description") or "")
    if "P1" not in desc:
        out["description"] = (
            "P1: local/scripted OCR and scripted structure. "
            "Cloud LLM keys and Ollama are unused. " + desc
        )
    for inp in out.get("inputs") or []:
        if not isinstance(inp, dict):
            continue
        name = inp.get("name")
        if name == "llm_provider":
            inp["default"] = "none"
        elif name == "ocr_engine":
            inp["default"] = "tesseract"
        elif name in {
            "ollama_base_url",
            "ollama_model",
            "deepseek_api_key",
            "openrouter_api_key",
            "anthropic_api_key",
            "vector_db_url",
            "deepseek_model",
            "openrouter_model",
            "anthropic_model",
        }:
            inp["default"] = ""
        elif name == "store_captures":
            inp["default"] = False
    return out


def apply_p1_cloned_block(workspace: Any, bid: str) -> bool:
    if bid != "capture":
        return False
    dest = Path("vendor") / "blocks" / "capture"
    workspace.write_text(dest / "block.py", P1_CAPTURE_ADAPTER)
    meta = dest / "block.json"
    if workspace.exists(meta):
        data = json.loads(workspace.read_text(meta))
        workspace.write_text(
            meta,
            json.dumps(apply_p1_capture_manifest(data), indent=2, sort_keys=True)
            + "\n",
        )
    return True


def _posture_file(root: Path, rel: str, fallback: Path | None) -> Path | None:
    candidate = Path(root) / rel
    if candidate.is_file():
        return candidate
    if fallback is not None:
        alt = Path(fallback) / rel
        if alt.is_file():
            return alt
    return None


def assert_workspace_posture(root: Path, fallback: Path | None = None) -> None:
    """Fail closed if a RoleRunner tree does not declare exactly P1.

    *fallback* is the committed destination when WRITER is staged: a rework
    pass does not rewrite README, so the check must see the previous stamp.
    """
    base = Path(root)
    findings: List[str] = []
    missing = [rel for rel in DELIVERY_ARTIFACTS if _posture_file(base, rel, fallback) is None]
    if missing:
        raise PostureError("missing posture artifacts: " + ", ".join(missing))

    doc_path = _posture_file(base, str(POSTURE_DOC), fallback)
    doc = json.loads(doc_path.read_text(encoding="utf-8"))
    if doc.get("posture") != NETWORK_POSTURE:
        findings.append(
            f"{POSTURE_DOC}: posture {doc.get('posture')!r} != {NETWORK_POSTURE!r}"
        )
    if NETWORK_POSTURE_REASON not in str(doc.get("reason") or ""):
        findings.append(f"{POSTURE_DOC}: reason does not match NETWORK_POSTURE_REASON")

    for rel in DELIVERY_ARTIFACTS:
        text = _posture_file(base, rel, fallback).read_text(encoding="utf-8")
        if NETWORK_POSTURE not in text:
            findings.append(f"{rel}: does not name {NETWORK_POSTURE}")
        if rel == "render.yaml":
            lowered = text.lower()
            for forbidden in ("postgres", "keyvalue", "redis", "fromdatabase"):
                if forbidden in lowered:
                    findings.append(f"{rel}: P1 forbids {forbidden}")
        if rel in {".env.example", "README.md", "app/main.py", "Dockerfile"}:
            for token in P1_FORBIDDEN:
                if token in text:
                    findings.append(f"{rel}: P1 forbids {token}")
    if findings:
        raise PostureError("; ".join(findings))


def scan_disagreements(texts: Iterable[Tuple[str, str]]) -> List[str]:
    other = {"P2", "P3"} - {NETWORK_POSTURE}
    hits: List[str] = []
    for loc, text in texts:
        for token in other:
            if f'NETWORK_POSTURE = "{token}"' in text or f'"posture": "{token}"' in text:
                hits.append(f"{loc}: declares {token}")
    return hits
