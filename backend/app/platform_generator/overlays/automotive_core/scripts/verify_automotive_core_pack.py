"""Live-gate verification for the Automotive Core RAG foundation pack.

Checks row counts, embedding identity, index presence, and retrieval quality
against the isolated PostgreSQL instance configured via DATABASE_URL.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from sqlalchemy import create_engine, text

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _db_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


def _count_rows(engine, project_id: str) -> int:
    table = os.getenv("RAG_VECTOR_NAMESPACE", "v2")
    table_name = f"chunks_{table}" if table else "chunks"
    with engine.connect() as conn:
        return int(
            conn.execute(
                text(f"SELECT count(*) FROM {table_name} WHERE project_id = :project_id"),
                {"project_id": project_id},
            ).scalar()
        )


def _embedding_identity(engine) -> dict:
    table = os.getenv("RAG_VECTOR_NAMESPACE", "v2")
    table_name = f"chunks_{table}" if table else "chunks"
    with engine.connect() as conn:
        row = conn.execute(
            text(
                f"SELECT embedding_model, embedding_dim, embedding_normalized "
                f"FROM {table_name} LIMIT 1"
            )
        ).first()
        if row is None:
            return {}
        return {
            "model": row.embedding_model,
            "dim": row.embedding_dim,
            "normalized": row.embedding_normalized,
        }


def _indexes_present(engine) -> list[str]:
    table = os.getenv("RAG_VECTOR_NAMESPACE", "v2")
    table_name = f"chunks_{table}" if table else "chunks"
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT indexname FROM pg_indexes WHERE tablename = :table_name"
            ),
            {"table_name": table_name},
        ).all()
        return [r[0] for r in rows]


def _verify_retrieval(project_id: str, queries: dict[str, str]) -> dict:
    from app.core.automotive_retrieval import (
        retrieve_by_campaign_number,
        retrieve_foundation_evidence,
    )

    results: dict = {}
    for name, query in queries.items():
        if name == "exact_campaign":
            evidence = retrieve_by_campaign_number(query)
        else:
            evidence = retrieve_foundation_evidence(query, top_k=5)
        results[name] = {
            "query": query,
            "returned": len(evidence),
            "top_reference": evidence[0].record_reference if evidence else None,
            "top_score": evidence[0].retrieval_score if evidence else None,
            "knowledge_layer": evidence[0].knowledge_layer if evidence else None,
        }
    return results


def _discover_sample_campaigns(engine, project_id: str, limit: int = 20) -> list[str]:
    import re

    table = os.getenv("RAG_VECTOR_NAMESPACE", "v2")
    table_name = f"chunks_{table}" if table else "chunks"
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"SELECT text FROM {table_name} "
                "WHERE project_id = :project_id LIMIT :limit"
            ),
            {"project_id": project_id, "limit": limit},
        ).all()
    campaigns = []
    for row in rows:
        m = re.search(r"Campaign:\s*(\S+)", row.text)
        if m and m.group(1) not in campaigns:
            campaigns.append(m.group(1))
    return campaigns


def _find_vehicle_record(engine, project_id: str, make: str) -> tuple[str, str]:
    """Return (campaign, vehicle_line) for a record containing the make."""
    table = os.getenv("RAG_VECTOR_NAMESPACE", "v2")
    table_name = f"chunks_{table}" if table else "chunks"
    with engine.connect() as conn:
        row = conn.execute(
            text(
                f"SELECT text FROM {table_name} "
                "WHERE project_id = :project_id AND text ILIKE :pattern LIMIT 1"
            ),
            {"project_id": project_id, "pattern": f"%Make: {make}%"},
        ).first()
    if row is None:
        return "", ""
    import re

    campaign = re.search(r"Campaign:\s*(\S+)", row.text)
    year = re.search(r"Year:\s*(\S+)", row.text)
    make_m = re.search(r"Make:\s*(\S+)", row.text)
    model = re.search(r"Model:\s*(\S+)", row.text)
    vehicle = " ".join(
        filter(
            None,
            [
                year.group(1) if year else None,
                make_m.group(1) if make_m else make,
                model.group(1) if model else None,
            ],
        )
    )
    return campaign.group(1) if campaign else "", vehicle


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify automotive core pack")
    parser.add_argument("--project-id", default="automotive_core_v1")
    args = parser.parse_args()

    url = _db_url()
    engine = create_engine(url)

    report: dict = {"database_url_masked": url.replace(url.split("://")[1].split("@")[0], "***") if "@" in url else url}

    count = _count_rows(engine, args.project_id)
    report["row_count"] = count
    logger.info("chunks_v2 rows for %s: %d", args.project_id, count)

    identity = _embedding_identity(engine)
    report["embedding_identity"] = identity
    logger.info("embedding identity: %s", identity)

    indexes = _indexes_present(engine)
    report["indexes"] = indexes
    logger.info("indexes: %s", indexes)

    sample_campaigns = _discover_sample_campaigns(engine, args.project_id)
    report["sample_campaigns"] = sample_campaigns[:5]
    logger.info("sample campaigns: %s", sample_campaigns[:5])

    exact_campaign = sample_campaigns[0] if sample_campaigns else ""
    vehicle_campaign, vehicle_line = _find_vehicle_record(engine, args.project_id, "HONDA")
    if not vehicle_campaign:
        vehicle_campaign, vehicle_line = _find_vehicle_record(engine, args.project_id, "TOYOTA")
    if not vehicle_campaign and sample_campaigns:
        vehicle_campaign = sample_campaigns[0]
        vehicle_line = "vehicle"

    queries = {
        "exact_campaign": exact_campaign,
        "vehicle_lookup": vehicle_line,
        "component_lookup": "air bag",
        "unsupported": "99V999000",
    }
    retrieval = _verify_retrieval(args.project_id, queries)
    report["retrieval"] = retrieval
    for name, result in retrieval.items():
        logger.info("%s: %s", name, result)

    print(json.dumps(report, indent=2))

    failures = []
    if count == 0:
        failures.append("no indexed rows")
    if identity.get("dim") != 384:
        failures.append(f"unexpected embedding dimension: {identity.get('dim')}")
    if identity.get("model") != "BAAI/bge-small-en-v1.5":
        failures.append(f"unexpected embedding model: {identity.get('model')}")
    if not any("fts_gin" in idx for idx in indexes):
        failures.append("missing BM25/GIN index")
    if not any("project" in idx for idx in indexes):
        failures.append("missing project index")

    exact = retrieval.get("exact_campaign", {})
    if exact.get("top_reference") != exact_campaign:
        failures.append(f"exact campaign lookup did not return {exact_campaign}")

    vehicle = retrieval.get("vehicle_lookup", {})
    if vehicle_campaign and vehicle_campaign not in (vehicle.get("top_reference") or ""):
        failures.append(f"vehicle lookup did not return {vehicle_campaign}")

    component = retrieval.get("component_lookup", {})
    if not component.get("returned"):
        failures.append("component lookup returned no evidence")

    unsupported = retrieval.get("unsupported", {})
    if unsupported.get("top_reference") == "99V999000":
        failures.append("unsupported campaign fabricated a match")

    if failures:
        logger.error("VERIFICATION FAILED: %s", failures)
        return 1

    logger.info("VERIFICATION PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
