"""Delivered-product network posture. Exactly one of P1/P2/P3.

P1 — no outbound runtime (product claims offline; factory host is separate).
P2 — allowlisted egress only.
P3 — open egress.

The factory delivery pipeline (RoleRunner) emits P1 products. Changing this
value requires amending every generated artifact (Dockerfile, README,
.env.example, render.yaml, app/main.py, docs/network_posture.json).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

# Exactly one. Do not add a second.
NETWORK_POSTURE = "P1"
NETWORK_POSTURE_REASON = (
    "Delivered platforms run in-process against vendored blocks; "
    "no Store URL, no outbound HTTP at runtime."
)

POSTURE_DOC = Path("docs") / "network_posture.json"

#: RoleRunner files that must name the chosen posture.
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

#: Tokens a P1 RoleRunner tree must not offer as runtime wiring.
P1_FORBIDDEN: Tuple[str, ...] = (
    "CEREBRUM_API_URL",
    "CEREBRUM_API_KEY",
    "/v1/execute",
)

#: ProductGenerator.generate() still documents store URL / estate Postgres.
#: Declared, not silently dropped, not the delivery posture.
DECLARED_GENERATOR_POSTURE_EXCEPTIONS: Tuple[str, ...] = (
    "ProductGenerator._write_env_example documents CEREBRUM_API_URL "
    "(template-path handlers POST to the store)",
    "ProductGenerator._write_runtime_packaging emit estate Postgres / FastEmbed "
    "(resident-engineer extra, not RoleRunner delivery)",
)


class PostureError(ValueError):
    """Artifacts disagree with the chosen network posture."""


def declaration() -> Dict[str, str]:
    return {
        "schema_version": "network_posture.v1",
        "posture": NETWORK_POSTURE,
        "reason": NETWORK_POSTURE_REASON,
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


def assert_workspace_posture(root: Path) -> None:
    """Fail closed if a RoleRunner tree does not declare exactly P1."""
    base = Path(root)
    findings: List[str] = []
    missing = [rel for rel in DELIVERY_ARTIFACTS if not (base / rel).is_file()]
    if missing:
        raise PostureError("missing posture artifacts: " + ", ".join(missing))

    doc = json.loads((base / POSTURE_DOC).read_text(encoding="utf-8"))
    if doc.get("posture") != NETWORK_POSTURE:
        findings.append(
            f"{POSTURE_DOC}: posture {doc.get('posture')!r} != {NETWORK_POSTURE!r}"
        )
    if doc.get("reason") != NETWORK_POSTURE_REASON:
        findings.append(f"{POSTURE_DOC}: reason does not match NETWORK_POSTURE_REASON")

    for rel in DELIVERY_ARTIFACTS:
        text = (base / rel).read_text(encoding="utf-8")
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
    """Return artifacts that name a different posture than NETWORK_POSTURE."""
    other = {"P2", "P3"} - {NETWORK_POSTURE}
    hits: List[str] = []
    for loc, text in texts:
        for token in other:
            if f"NETWORK_POSTURE = \"{token}\"" in text or f"posture\": \"{token}\"" in text:
                hits.append(f"{loc}: declares {token}")
    return hits
