"""Load the fictional Northstar fixture pack into the operational store.

Two canonical projects are created and their documents ingested with document
roles (used by schema-driven context enrichment). All data is fictional.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict

from sqlalchemy.orm import Session

from app.retailops import bootstrap
from app.retailops.ingestion import ingest_document

_HERE = Path(__file__).resolve().parent / "northstar"

TENANT_ID = "northstar"
TENANT_NAME = "Northstar Global Retail"

# Canonical project name -> fixture subdirectory. Stable canonical names let
# tests resolve generated project IDs by name.
PROJECTS: Dict[str, str] = {
    "Northstar Riyadh Region": "riyadh",
    "Northstar Melbourne Region": "melbourne",
}

# Default pilot principals with retail permissions.
MANAGER_ID = "u_ops_manager"
VIEWER_ID = "u_viewer"


def _role_for(filename: str) -> str:
    name = filename.lower()
    if name.startswith("sop") or "sop_" in name:
        return "policy"
    if "promotion_rules" in name:
        return "promotion_rules"
    if "promotion_evidence" in name or "evidence" in name:
        return "promotion_evidence"
    if "supplier" in name:
        return "supplier"
    if "incident" in name:
        return "incident"
    if "inventory" in name:
        return "inventory"
    return "document"


def load_fixtures(session: Session, tenant_id: str = TENANT_ID) -> Dict[str, str]:
    """Ingest the fixture pack. Returns {canonical_name: project_id}."""
    bootstrap.ensure_tenant(session, tenant_id, TENANT_NAME)
    bootstrap.ensure_user(
        session, MANAGER_ID, tenant_id,
        name="Ops Manager", permissions=["retail:read", "retail:analyse"],
    )
    bootstrap.ensure_user(
        session, VIEWER_ID, tenant_id, name="Viewer", permissions=["retail:read"],
    )

    project_ids: Dict[str, str] = {}
    for canonical_name, subdir in PROJECTS.items():
        project = bootstrap.ensure_project(
            session, tenant_id=tenant_id, canonical_name=canonical_name
        )
        project_ids[canonical_name] = project.id
        folder = _HERE / subdir
        # Skip CSV when an equivalent XLSX exists to avoid duplicate rows; both
        # are ingested to demonstrate every format, but from different projects
        # if desired. Here we ingest all files (formats coverage).
        for path in sorted(folder.iterdir()):
            if not path.is_file():
                continue
            data = path.read_bytes()
            doc = ingest_document(
                session,
                tenant_id=tenant_id,
                project_id=project.id,
                filename=path.name,
                data=data,
                source_uri=f"fixtures://{subdir}/{path.name}",
            )
            meta = dict(doc.meta or {})
            meta["doc_role"] = _role_for(path.name)
            doc.meta = meta
        session.flush()
    return project_ids
