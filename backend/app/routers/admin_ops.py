"""Admin ops the owner runs from the settings page: switch the coder model,
reboot the service. Master-key only — the same gate as backups/metrics.

Why this exists: when the coding model stops responding, the only lever used
to be an env var on ECS the owner could not reach (live 2026-10-02, the
DeepSeek coder hung for hours). These two buttons put that lever in the owner's
hands: pick a different model and apply it, or restart the whole service.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.auth import Principal, require_master_key
from app.core import runtime_settings

logger = logging.getLogger(__name__)

router = APIRouter()

#: Providers the headless coder CLI is known to accept. The model is free text
#: (the operator names the exact id their provider serves), but the provider is
#: a short known set so the picker can offer it.
KNOWN_PROVIDERS = ("deepseek", "openrouter", "anthropic", "kimi")


class CoderModelBody(BaseModel):
    model: Optional[str] = None
    provider: Optional[str] = None


def _effective() -> Dict[str, Any]:
    from app.factory.build.codewhale_worker import worker_provider
    from app.factory.code_cli import deepseek_code_model

    return {"model": deepseek_code_model(), "provider": worker_provider()}


@router.get("/coder-model")
def get_coder_model(_admin: Principal = Depends(require_master_key)) -> Dict[str, Any]:
    """What the next build will use, which overrides are set, and the options."""
    overrides = runtime_settings.get_all()
    return {
        "ok": True,
        "effective": _effective(),
        "overrides": overrides,
        "provider_options": list(KNOWN_PROVIDERS),
        "note": "model is the exact id your provider serves; API keys stay in the deploy env",
    }


@router.post("/coder-model")
def set_coder_model(
    body: CoderModelBody, _admin: Principal = Depends(require_master_key)
) -> Dict[str, Any]:
    """Set (or clear, with a blank value) the coder model and/or provider. Takes
    effect on the NEXT build — no redeploy. A blank field clears that override."""
    changed = []
    if body.model is not None:
        runtime_settings.set_value(runtime_settings.CODER_MODEL, body.model)
        changed.append("model")
    if body.provider is not None:
        runtime_settings.set_value(runtime_settings.CODER_PROVIDER, body.provider)
        changed.append("provider")
    logger.info("admin set coder %s -> %s", changed, runtime_settings.get_all())
    return {
        "ok": True,
        "changed": changed,
        "effective": _effective(),
        "overrides": runtime_settings.get_all(),
        "message": "Applied. The next build uses it — no redeploy needed. "
        "Re-run the writer to pick it up.",
    }


@router.post("/reboot")
def reboot(_admin: Principal = Depends(require_master_key)) -> Dict[str, Any]:
    """Restart the service. The process exits shortly AFTER this response is
    sent; the orchestrator (ECS) brings a fresh task up in its place, clearing
    any wedged model-client state. There is a brief gap while it restarts."""

    def _bye() -> None:
        time.sleep(1.5)  # let the HTTP response flush first
        logger.warning("admin-triggered reboot: exiting so the orchestrator restarts")
        os._exit(0)

    threading.Thread(target=_bye, daemon=True).start()
    return {
        "ok": True,
        "message": "Rebooting now — the service will be back in a moment. "
        "Refresh in ~30–60s.",
    }
