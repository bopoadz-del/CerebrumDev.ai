from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routers import sessions, config

app = FastAPI(title="CerebrumDev.ai API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sessions.router, prefix="/v1/sessions", tags=["sessions"])
app.include_router(config.router, prefix="/v1/sessions", tags=["config"])

@app.get("/health")
async def health():
    return {"status": "ok"}
