"""product_blueprint.v1 — fail-closed schema for Factory generation."""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class BlueprintError(ValueError):
    """Invalid product blueprint."""


class CapabilityStrategyHint(str, Enum):
    REUSE = "REUSE"
    ADAPT = "ADAPT"
    COMPOSE = "COMPOSE"
    GENERATE = "GENERATE"
    STUB = "STUB"
    UNSUPPORTED = "UNSUPPORTED"


class CapabilitySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    block_ids: List[str] = Field(default_factory=list)
    strategy_hint: Optional[CapabilityStrategyHint] = None
    required: bool = True


class ProductBlueprint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    product_id: str
    product_name: str
    vertical: str
    summary: str
    capabilities: List[CapabilitySpec]
    ui_modules: List[str] = Field(default_factory=list)
    connectors: List[str] = Field(default_factory=list)
    edge_profile: str = "standard"
    human_authority: bool = True

    @field_validator("schema_version")
    @classmethod
    def _schema(cls, v: str) -> str:
        if v != "product_blueprint.v1":
            raise ValueError(f"unsupported schema_version '{v}'; expected product_blueprint.v1")
        return v

    @field_validator("product_id")
    @classmethod
    def _pid(cls, v: str) -> str:
        import re

        if not re.fullmatch(r"[a-z][a-z0-9_-]*", v):
            raise ValueError("product_id must be lowercase kebab/snake")
        return v

    @field_validator("capabilities")
    @classmethod
    def _caps(cls, v: List[CapabilitySpec]) -> List[CapabilitySpec]:
        if not v:
            raise ValueError("capabilities must be non-empty")
        ids = [c.id for c in v]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate capability ids")
        return v


def load_blueprint(path: Path | str) -> ProductBlueprint:
    p = Path(path)
    raw = p.read_text(encoding="utf-8")
    if p.suffix.lower() in {".yaml", ".yml"}:
        data = yaml.safe_load(raw)
    else:
        data = json.loads(raw)
    if not isinstance(data, dict):
        raise BlueprintError("blueprint root must be a mapping")
    try:
        return ProductBlueprint.model_validate(data)
    except Exception as exc:  # pydantic ValidationError
        raise BlueprintError(str(exc)) from exc


def blueprint_to_dict(bp: ProductBlueprint) -> Dict[str, Any]:
    return bp.model_dump(mode="json")
