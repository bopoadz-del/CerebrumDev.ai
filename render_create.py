"""Create CerebrumDev.ai services on Render via the Render API.

Set RENDER_API_KEY and RENDER_OWNER_ID env vars before running.
"""
import json
import os
import sys
import urllib.request
import urllib.error

RENDER_API_KEY = os.getenv("RENDER_API_KEY", "rnd_FNAX8sgCiqOWBOTqXbUjkN2jAPaN")
OWNER_ID = os.getenv("RENDER_OWNER_ID", "")
REPO = "https://github.com/bopoadz-del/CerebrumDev.ai"

if not OWNER_ID:
    print("ERROR: Set RENDER_OWNER_ID environment variable")
    sys.exit(1)

BACKEND_VARS = [
    {"key": "ENV", "value": "production"},
    {"key": "CEREBRUM_API_URL", "value": "https://cerebrum-blocks.onrender.com"},
    {"key": "CEREBRUM_API_KEY", "value": os.getenv("CEREBRUM_API_KEY", "")},
    {"key": "CEREBRUM_LLM_API_KEY", "value": os.getenv("CEREBRUM_LLM_API_KEY", "")},
    {"key": "STORAGE_PATH", "value": "/app/storage"},
    {"key": "PYTHONIOENCODING", "value": "utf-8"},
]


def create_service(payload):
    req = urllib.request.Request(
        "https://api.render.com/v1/services",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {RENDER_API_KEY}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}")
        print(e.read().decode())
        sys.exit(1)


backend_payload = {
    "type": "web_service",
    "name": "cerebrumdev-backend",
    "ownerId": OWNER_ID,
    "repo": REPO,
    "autoDeploy": "yes",
    "branch": "master",
    "serviceDetails": {
        "env": "docker",
        "plan": "starter",
        "region": "oregon",
        "envSpecificDetails": {"dockerfilePath": "backend/Dockerfile"},
        "healthCheckPath": "/health",
        "numInstances": 1,
    },
    "envVars": BACKEND_VARS,
}

frontend_payload = {
    "type": "static_site",
    "name": "cerebrumdev-frontend",
    "ownerId": OWNER_ID,
    "repo": REPO,
    "autoDeploy": "yes",
    "branch": "master",
    "serviceDetails": {
        "buildCommand": "cd frontend && npm install && npm run build",
        "publishPath": "frontend/dist",
    },
    "envVars": [
        {"key": "VITE_API_URL", "value": "https://cerebrumdev-backend.onrender.com"},
    ],
}

print("Creating backend service...")
backend = create_service(backend_payload)
print(json.dumps(backend, indent=2))

print("\nCreating frontend service...")
frontend = create_service(frontend_payload)
print(json.dumps(frontend, indent=2))
