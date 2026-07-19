"""Generated FastAPI entrypoint for cerebrum-steward."""

from fastapi import FastAPI

app = FastAPI(title="Cerebrum Steward", version="1.0.0")


@app.get("/health")
def health():
    return {
        "ok": True,
        "product_id": "cerebrum-steward",
        "vertical": "estate",
        "human_authority": True,
    }


@app.get("/v1/capabilities")
def capabilities():
    import json
    from pathlib import Path
    plan = json.loads((Path(__file__).resolve().parents[1] / "factory_plan.json").read_text())
    return plan


@app.get("/v1/agents")
def agents():
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parent / "agents" / "manifests"
    return [json.loads(p.read_text()) for p in sorted(root.glob("*.json"))]


@app.get("/v1/workflows")
def workflows():
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parent / "workflows" / "workflows.json"
    return json.loads(path.read_text())
