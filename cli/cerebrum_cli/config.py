"""Config layer for cerebrum_cli.

Resolution order: CLI flags > environment variables > ~/.cerebrum/config.toml.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


CONFIG_DIR = Path.home() / ".cerebrum"
CONFIG_PATH = CONFIG_DIR / "config.toml"

DEFAULT_CONFIG: dict[str, Any] = {
    "base_url": "http://127.0.0.1:8000",
    "api_key": "",
    "domain": "construction",
    "instance_name": "local",
    "session_id": "",
}


def _load_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_config(
    base_url: str | None = None,
    api_key: str | None = None,
    domain: str | None = None,
    instance_name: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Merge config sources: flag > env > config file > defaults."""
    cfg = DEFAULT_CONFIG.copy()
    cfg.update(_load_file(CONFIG_PATH))

    env_overrides = {
        "base_url": os.getenv("CEREBRUM_BASE_URL"),
        "api_key": os.getenv("CEREBRUM_API_KEY"),
        "domain": os.getenv("CEREBRUM_DOMAIN"),
        "instance_name": os.getenv("CEREBRUM_INSTANCE_NAME"),
        "session_id": os.getenv("CEREBRUM_SESSION_ID"),
    }
    cfg.update({k: v for k, v in env_overrides.items() if v is not None})

    flag_overrides = {
        "base_url": base_url,
        "api_key": api_key,
        "domain": domain,
        "instance_name": instance_name,
        "session_id": session_id,
    }
    cfg.update({k: v for k, v in flag_overrides.items() if v is not None})

    return cfg


def mask(value: str | None, visible: int = 4) -> str:
    """Mask a secret for display."""
    if not value:
        return "(not set)"
    if len(value) <= visible * 2:
        return "*" * len(value)
    return value[:visible] + "..." + value[-visible:]


def save_config(cfg: dict[str, Any]) -> None:
    """Write config to ~/.cerebrum/config.toml."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    lines = ["# Cerebrum CLI configuration", ""]
    for key in DEFAULT_CONFIG:
        value = cfg.get(key, DEFAULT_CONFIG[key])
        lines.append(f'{key} = {repr(value)}')
    CONFIG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
