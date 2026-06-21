"""Inject the deployed-session router into Cerebrum-Blocks app/main.py."""
from pathlib import Path

main = Path("app/main.py")
text = main.read_text(encoding="utf-8")

if "deployed" in text:
    print("deployed router already present")
    raise SystemExit(0)

# Add import inside the existing from app.routers import block
marker = "from app.routers import (\n"
if marker in text:
    text = text.replace(marker, marker + "    deployed,\n")
else:
    # Fallback: insert after the import block
    text = text.replace(
        "from app.routers.metrics import _record_metrics",
        "from app.routers.metrics import _record_metrics\nfrom app.routers import deployed",
    )
    text = text.replace(
        "app.include_router(workflow.router)\n",
        "app.include_router(workflow.router)\napp.include_router(deployed.router, prefix=\"/v1/deployed\", tags=[\"deployed\"])\n",
    )
    main.write_text(text, encoding="utf-8")
    raise SystemExit(0)

# Add router include after workflow router
text = text.replace(
    "app.include_router(workflow.router)\n",
    "app.include_router(workflow.router)\napp.include_router(deployed.router, prefix=\"/v1/deployed\", tags=[\"deployed\"])\n",
)

main.write_text(text, encoding="utf-8")
print("patched app/main.py with deployed router")
