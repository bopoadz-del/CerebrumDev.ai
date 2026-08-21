import logging
import os
import sys

from dotenv import load_dotenv


def _configure_logging() -> None:
    """Send this application's logs to stdout.

    Nothing configured logging, so uvicorn's own loggers printed access lines
    while every ``logging.getLogger("cerebrumdev...")`` call went nowhere --
    the root logger had no handler and defaulted to WARNING. A twenty-minute
    background build was therefore completely unobservable in production: no
    coder calls, no phase transitions, no failures, only access logs. Level
    is LOG_LEVEL (default INFO); uvicorn keeps its own handlers.
    """
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    root = logging.getLogger()
    if not any(getattr(h, "_cerebrum_stdout", False) for h in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(levelname)s %(name)s: %(message)s")
        )
        handler._cerebrum_stdout = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    root.setLevel(getattr(logging, level, logging.INFO))


_configure_logging()

# Load .env before any module reads environment variables
load_dotenv()

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

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
    resend_verification,
)
from .resident_engineer.router import router as resident_engineer_router
from .change_requests.router import router as change_requests_router
from .workbench.router import router as workbench_router
from .workbench.flags import build_mode_enabled, kimi_workbench_enabled
from .resident_engineer.flags import resident_engineer_enabled

verify_production_auth()

app = FastAPI(title="CerebrumDev.ai API", version="0.1.0")
