import os
from dotenv import load_dotenv

# Load .env before any module reads environment variables
load_dotenv()

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .core.auth import require_api_key, verify_production_auth
from .routers import (
    sessions,
    config,
    domains,
    upload,
    chat,
    deploy,
    train,
    factory_drive,
    product_factory,
    session_product,
)
from .resident_engineer.router import router as resident_engineer_router
from .change_requests.router import router as change_requests_router

verify_production_auth()

app = FastAPI(title="CerebrumDev.ai API", version="0.1.0")

_frontend_url = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")
_cors_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ALLOW_ORIGINS",
        f"{_frontend_url},https://cerebrumdev-frontend.onrender.com",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sessions.router, prefix="/v1/sessions", tags=["sessions"], dependencies=[Depends(require_api_key)])
app.include_router(config.router, prefix="/v1/sessions", tags=["config"], dependencies=[Depends(require_api_key)])
app.include_router(domains.router, prefix="/v1/domains", tags=["domains"], dependencies=[Depends(require_api_key)])
app.include_router(upload.router, prefix="/v1/sessions", tags=["upload"], dependencies=[Depends(require_api_key)])
app.include_router(chat.router, prefix="/v1/sessions", tags=["chat"], dependencies=[Depends(require_api_key)])
app.include_router(deploy.router, prefix="/v1/sessions", tags=["deploy"], dependencies=[Depends(require_api_key)])
app.include_router(train.router, prefix="/v1/sessions", tags=["training"], dependencies=[Depends(require_api_key)])
app.include_router(factory_drive.router, prefix="/v1/sessions", tags=["factory-drive"], dependencies=[Depends(require_api_key)])
app.include_router(factory_drive.callback_router)
app.include_router(
    product_factory.router,
    prefix="/v1/factory/product",
    tags=["product-factory"],
    dependencies=[Depends(require_api_key)],
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


@app.get("/health")
async def health():
    storage = _probe_storage()
    redis = _probe_redis()
    return {
        "status": "ok" if storage.get("ok") else "degraded",
        "storage": storage,
        "redis": redis,
        "resident_engineer_enabled": os.getenv("RESIDENT_ENGINEER_ENABLED", "false"),
    }


@app.get("/ready")
async def ready():
    storage = _probe_storage()
    blocks = _probe_blocks()
    llm_configured = bool(
        os.getenv("KIMI_API_KEY")
        or os.getenv("CEREBRUM_LLM_API_KEY")
        or os.getenv("OLLAMA_API_KEY")
        or os.getenv("QWEN_API_KEY")
        or os.getenv("LLM_PROVIDER")
        or os.getenv("KIMI_MOCK")
    )
    checks = {
        "storage": bool(storage.get("ok")),
        "cerebrum_blocks": bool(blocks.get("ok")),
        "llm_configured": llm_configured,
        "api_key_configured": bool(os.getenv("CEREBRUM_DEV_API_KEY"))
        or os.getenv("ENV", "development") != "production",
    }
    # Storage + API key are hard requirements; blocks/LLM may be degraded in local dev.
    ready_ok = checks["storage"] and checks["api_key_configured"]
    return {
        "status": "ready" if ready_ok else "not_ready",
        "checks": checks,
        "details": {"storage": storage, "cerebrum_blocks": blocks},
    }
