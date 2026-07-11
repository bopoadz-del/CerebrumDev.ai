import os
from dotenv import load_dotenv

# Load .env before any module reads environment variables
load_dotenv()

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .core.auth import require_api_key, verify_production_auth
from .routers import sessions, config, domains, upload, chat, deploy, train, factory_drive

verify_production_auth()

app = FastAPI(title="CerebrumDev.ai API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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

@app.get("/health")
async def health():
    return {"status": "ok"}
