import os
from dotenv import load_dotenv

# Load .env before any module reads environment variables
load_dotenv()

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .core.request_limits import BodySizeLimitMiddleware
from .core import backup_scheduler
from .core.auth import require_api_key, verify_production_auth
from .core.billing import require_entitled
from .routers import (
    accounts,
    billing,
    sessions,
    config,
    domains,
    upload,
    chat,
    deploy,
    factory_drive,
    product_factory,
    session_product,
    delivery_standard,
)
from .resident_engineer.router import router as resident_engineer_router
from .change_requests.router import router as change_requests_router
from .workbench.router import router as workbench_router
from .workbench.flags import build_mode_enabled, kimi_workbench_enabled
from .resident_engineer.flags import resident_engineer_enabled

verify_production_auth()

app = FastAPI(title="CerebrumDev.ai API", version="0.1.0")


# Nightly backups run in-process: Render cron jobs cannot mount persistent
# disks and a disk belongs to exactly one service, so this process is the only
# one that can read /app/storage at all. See core/backup_scheduler.py.
@app.on_event("startup")
async def _arm_backup_scheduler() -> None:
    app.state.backup_task = backup_scheduler.start()


@app.on_event("shutdown")
async def _disarm_backup_scheduler() -> None:
    task = getattr(app.state, "backup_task", None)
    if task is not None:
        task.cancel()

_frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")
_cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ALLOW_ORIGINS",
        f"{_frontend_url},https://cerebrumdev-frontend.onrender.com",
    ).split(",")
    if origin.strip()
]

# Body-size ceiling. Added BEFORE the CORS block on purpose: Starlette runs the
# last-added middleware outermost, so registering this first leaves CORS on the
# outside and the 413 comes back with the CORS headers attached. Reversed, the
# browser reports an opaque CORS failure instead of "file too large".
app.add_middleware(BodySizeLimitMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Public account endpoints (register/login/verify carry no credential; me/keys
# enforce their own account principal).
app.include_router(accounts.router, prefix="/v1/auth", tags=["auth"])
# Billing status self-enforces an account credential; checkout/webhook join here.
app.include_router(billing.router, prefix="/v1/billing", tags=["billing"])
app.include_router(sessions.router, prefix="/v1/sessions", tags=["sessions"], dependencies=[Depends(require_api_key)])
app.include_router(config.router, prefix="/v1/sessions", tags=["config"], dependencies=[Depends(require_api_key)])
app.include_router(domains.router, prefix="/v1/domains", tags=["domains"], dependencies=[Depends(require_api_key)])
app.include_router(upload.router, prefix="/v1/sessions", tags=["upload"], dependencies=[Depends(require_api_key)])
app.include_router(chat.router, prefix="/v1/sessions", tags=["chat"], dependencies=[Depends(require_api_key)])
app.include_router(deploy.router, prefix="/v1/sessions", tags=["deploy"], dependencies=[Depends(require_api_key)])
app.include_router(factory_drive.router, prefix="/v1/sessions", tags=["factory-drive"], dependencies=[Depends(require_api_key)])
app.include_router(factory_drive.callback_router)
# Factory runs are paid actions: require_entitled blocks expired trials with
# 402 when BILLING_ENFORCEMENT is on (it chains require_api_key).
app.include_router(
    product_factory.router,
    prefix="/v1/factory/product",
    tags=["product-factory"],
    dependencies=[Depends(require_entitled)],
)
# The coder's canonical brief: immutable standard + per-product domain pack.
app.include_router(
    delivery_standard.router,
    prefix="/v1/factory/delivery-standard",
    tags=["delivery-standard"],
    dependencies=[Depends(require_entitled)],
)
app.include_router(
    session_product.router,
    prefix="/v1/sessions",
    tags=["session-product"],
    dependencies=[Depends(require_api_key)],
)
app.include_router(
    resident_engineer_router,
    dependencies=[Depends(require_api_key)],
)
app.include_router(
    change_requests_router,
    dependencies=[Depends(require_api_key)],
)
app.include_router(
    workbench_router,
    dependencies=[Depends(require_api_key)],
)


def _probe_redis() -> dict:
    """Optional Redis/Key Value probe — unset REDIS_URL is fine."""
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        return {"configured": False}
    try:
        import redis  # type: ignore

        client = redis.from_url(url, socket_connect_timeout=1)
        client.ping()
        return {"configured": True, "ok": True}
    except ImportError:
        return {"configured": True, "ok": False, "error": "redis package not installed"}
    except Exception as exc:  # noqa: BLE001
        return {"configured": True, "ok": False, "error": str(exc)}


def _probe_storage() -> dict:
    storage = os.getenv("STORAGE_PATH", "./storage")
    try:
        os.makedirs(storage, exist_ok=True)
        probe = os.path.join(storage, ".healthcheck")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return {"ok": True, "path": storage}
    except OSError as exc:
        return {"ok": False, "path": storage, "error": str(exc)}


def _probe_blocks() -> dict:
    url = os.getenv("CEREBRUM_API_URL")
    if not url:
        return {"ok": False, "error": "CEREBRUM_API_URL unset"}
    try:
        import urllib.request

        req = urllib.request.Request(f"{url.rstrip('/')}/health", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return {"ok": 200 <= resp.status < 300, "status": resp.status}
    except Exception as exc:  # noqa: BLE001 - health probes must not raise
        return {"ok": False, "error": str(exc)}


def _probe_kimi_cli() -> dict:
    """Evaluated Kimi workbench capability: flag AND a CLI that answers.

    A true flag with no binary is configuration, not capability — health
    must never report it as enabled.
    """
    flag = kimi_workbench_enabled()
    probe: dict = {"flag_enabled": flag, "cli_ok": False}
    if not flag:
        return probe
    from app.factory.coder import code_cli_command

    cli = code_cli_command()
    try:
        import subprocess

        proc = subprocess.run(
            [cli, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        probe["cli_ok"] = proc.returncode == 0
        if proc.returncode == 0:
            probe["cli_version"] = (proc.stdout or "").strip()[:80]
        else:
            probe["error"] = (proc.stderr or "").strip()[:200] or f"exit {proc.returncode}"
    except FileNotFoundError:
        probe["error"] = f"CLI not found: {cli}"
    except Exception as exc:  # noqa: BLE001 - health probes must not raise
        probe["error"] = str(exc)[:200]
    return probe


@app.get("/health")
async def health():
    storage = _probe_storage()
    redis = _probe_redis()
    kimi = _probe_kimi_cli()
    return {
        "status": "ok" if storage.get("ok") else "degraded",
        "storage": storage,
        "redis": redis,
        "resident_engineer_enabled": resident_engineer_enabled(),
        "build_mode_enabled": build_mode_enabled(),
        # Evaluated capability, not configuration: enabled only when the
        # flag is on AND the CLI actually answers.
        "kimi_workbench_enabled": bool(kimi["flag_enabled"] and kimi["cli_ok"]),
        "kimi_workbench": kimi,
    }


@app.get("/ready")
async def ready():
    storage = _probe_storage()
    blocks = _probe_blocks()
    # KIMI_MOCK is a mock, not a configured LLM — reported separately.
    llm_configured = bool(
        os.getenv("KIMI_API_KEY")
        or os.getenv("CEREBRUM_LLM_API_KEY")
        or os.getenv("CEREBRUM_CHAT_LLM_API_KEY")
        or os.getenv("CEREBRUM_FACTORY_LLM_API_KEY")
        or os.getenv("LLM_PROVIDER")
    )
    checks = {
        "storage": bool(storage.get("ok")),
        "cerebrum_blocks": bool(blocks.get("ok")),
        "llm_configured": llm_configured,
        "llm_mock": bool(os.getenv("KIMI_MOCK")),
        "api_key_configured": bool(os.getenv("CEREBRUM_DEV_API_KEY"))
        or os.getenv("ENV", "development") != "production",
    }
    # Storage + API key are hard requirements; blocks/LLM may be degraded in local dev.
    ready_ok = checks["storage"] and checks["api_key_configured"]
    # Informational, not gating: a failed backup must page someone, not take
    # the API out of rotation.
    last_backup = backup_scheduler.last_status()
    body = {
        "status": "ready" if ready_ok else "not_ready",
        "checks": checks,
        "details": {
            "storage": storage,
            "cerebrum_blocks": blocks,
            "last_backup": {
                "ok": bool(last_backup.get("ok")),
                "at": last_backup.get("at"),
            }
            if last_backup
            else None,
        },
    }
    # Answer with a status code the platform can act on. This endpoint is the
    # Render health check: returning 200 while reporting "not_ready" means an
    # unwritable disk reads as healthy and no restart or alert ever happens.
    return JSONResponse(status_code=200 if ready_ok else 503, content=body)
