"""Pack load helpers — HTTPS-only full acquisition, licence fail-closed."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.steward.config import PLATFORM_PROJECT_ID, PLATFORM_TENANT_ID
from app.steward.ingestion import ingest_bytes
from app.steward.money import parse_money
from app.steward.models import (
    EstateProject,
    FacilityAsset,
    FleetVehicle,
    HospitalityIntent,
    SourceRecord,
    Tenant,
)

PACKS_ROOT = Path(__file__).resolve().parent

# Parked / rejected sources (issue #73) — do not acquire.
REJECTED_SOURCES: List[Dict[str, str]] = [
    {
        "source_id": "sofdog/hospitality-internal-queries",
        "reason": "French-only; not needed for English v1",
    },
    {
        "source_id": "himu1780/meridian-palace-training",
        "reason": "licence/provenance/quality not verified",
    },
    {
        "source_id": "ariefansclub/* cleaning datasets",
        "reason": "insufficient operational value",
    },
]


def _checksum_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ensure_platform_scope(session: Session) -> None:
    if session.get(Tenant, PLATFORM_TENANT_ID) is None:
        session.add(Tenant(id=PLATFORM_TENANT_ID, name="Platform"))
    if session.get(EstateProject, PLATFORM_PROJECT_ID) is None:
        session.add(
            EstateProject(
                id=PLATFORM_PROJECT_ID,
                tenant_id=PLATFORM_TENANT_ID,
                canonical_name="prebuilt_steward_core",
                name="Steward Service Core (platform)",
                knowledge_layer=1,
            )
        )
    session.flush()


def ensure_estate_scope(
    session: Session, *, tenant_id: str, estate_id: str, name: str
) -> None:
    if session.get(Tenant, tenant_id) is None:
        session.add(Tenant(id=tenant_id, name=tenant_id))
    if session.get(EstateProject, estate_id) is None:
        session.add(
            EstateProject(
                id=estate_id,
                tenant_id=tenant_id,
                canonical_name=estate_id,
                name=name,
                knowledge_layer=2,
            )
        )
    session.flush()


def load_manifest(pack_id: str) -> Dict[str, Any]:
    path = PACKS_ROOT / pack_id / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_licence_ok(manifest: Dict[str, Any], profile: str) -> None:
    licence = str(manifest.get("licence") or "").strip()
    if profile == "full" and (
        not licence or licence.lower() in {"unknown", "unclear", "proprietary-unknown"}
    ):
        raise RuntimeError(
            f"unknown/unclear licence blocks full ingestion for {manifest.get('rag_pack_id')}"
        )


def upsert_source_record(
    session: Session,
    manifest: Dict[str, Any],
    *,
    profile: str,
    checksum: str,
    document_count: int,
    chunk_count: int,
    record_count: int,
    force: bool,
) -> SourceRecord:
    release_id = str(manifest["release_id"])
    pack_id = str(manifest["rag_pack_id"])
    source_id = str(manifest["source_id"])

    existing = session.execute(
        select(SourceRecord).where(
            SourceRecord.release_id == release_id,
            SourceRecord.rag_pack_id == pack_id,
            SourceRecord.source_id == source_id,
        )
    ).scalar_one_or_none()
    if existing and not force:
        return existing
    if existing and force:
        session.delete(existing)
        session.flush()
    rec = SourceRecord(
        id=uuid.uuid4().hex,
        release_id=release_id,
        rag_pack_id=pack_id,
        collection_id=str(manifest.get("collection_id") or pack_id),
        source_id=source_id,
        source_name=str(manifest.get("source_name") or pack_id),
        publisher=manifest.get("publisher"),
        source_class=str(manifest.get("source_class") or "platform_curated"),
        authority_level=str(manifest.get("authority_level") or "advisory"),
        licence=str(manifest.get("licence") or "unknown"),
        original_url=manifest.get("original_url"),
        resolved_download_url=manifest.get("resolved_download_url"),
        acquired_at=datetime.now(timezone.utc),
        content_checksum=checksum,
        record_count=record_count,
        document_count=document_count,
        chunk_count=chunk_count,
        profile=profile,
        knowledge_layer=int(manifest.get("knowledge_layer") or 1),
        authority_kind=str(manifest.get("authority_kind") or "advisory"),
        meta={
            "rejected_sources": REJECTED_SOURCES,
            "research_urls": manifest.get("research_urls") or [],
        },
    )
    session.add(rec)
    session.flush()
    return rec


def load_service_core_mini(session: Session, *, force: bool = False) -> Dict[str, Any]:
    pack_id = "steward_service_core_open_v1"
    manifest = load_manifest(pack_id)
    _assert_licence_ok(manifest, "mini")
    ensure_platform_scope(session)
    mini_dir = PACKS_ROOT / pack_id / "mini"
    docs = sorted(mini_dir.glob("*.md"))
    blob = b"".join(p.read_bytes() for p in docs)
    checksum = _checksum_bytes(blob)
    source = upsert_source_record(
        session,
        manifest,
        profile="mini",
        checksum=checksum,
        document_count=0,
        chunk_count=0,
        record_count=len(docs),
        force=force,
    )
    ingested = []
    total_chunks = 0
    for path in docs:
        data = path.read_bytes()
        doc = ingest_bytes(
            session,
            tenant_id=PLATFORM_TENANT_ID,
            project_id=PLATFORM_PROJECT_ID,
            knowledge_layer=1,
            filename=path.name,
            data=data,
            content_type="text/markdown",
            rag_pack_id=pack_id,
            collection_id=str(manifest.get("collection_id") or pack_id),
            source_record_id=source.id,
            authority_kind=str(manifest.get("authority_kind") or "advisory"),
            cite_as_policy=True,
            document_id=f"{pack_id}:{path.stem}",
            meta={
                "platform_curated_template": True,
                "requires_estate_override": True,
                "not_governing_policy": True,
                "status": "platform_curated_template",
            },
        )
        ingested.append(doc.id)
        total_chunks += doc.chunk_count
    source.document_count = len(ingested)
    source.chunk_count = total_chunks
    return {"ok": True, "pack_id": pack_id, "documents": ingested, "chunks": total_chunks}


def load_hospitality_intents_mini(
    session: Session, *, force: bool = False
) -> Dict[str, Any]:
    pack_id = "steward_hospitality_intents_v1"
    manifest = load_manifest(pack_id)
    _assert_licence_ok(manifest, "mini")
    ensure_platform_scope(session)
    path = PACKS_ROOT / pack_id / "mini" / "intents.jsonl"
    data = path.read_bytes()
    checksum = _checksum_bytes(data)
    source = upsert_source_record(
        session,
        manifest,
        profile="mini",
        checksum=checksum,
        document_count=0,
        chunk_count=0,
        record_count=0,
        force=force,
    )
    count = 0
    for line in data.decode("utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        session.add(
            HospitalityIntent(
                id=uuid.uuid4().hex,
                tenant_id=PLATFORM_TENANT_ID,
                project_id=PLATFORM_PROJECT_ID,
                intent_label=str(row.get("intent") or "unknown"),
                utterance=str(row.get("utterance") or ""),
                language=str(row.get("language") or "en"),
                authority_kind="synthetic",
                cite_as_policy=False,
                source_record_id=source.id,
                meta={"note": "routing/evaluation only — not operating truth"},
            )
        )
        count += 1
    source.record_count = count
    return {"ok": True, "pack_id": pack_id, "intent_count": count}


def load_facilities_mini(session: Session, *, force: bool = False) -> Dict[str, Any]:
    pack_id = "steward_facilities_open_v1"
    manifest = load_manifest(pack_id)
    _assert_licence_ok(manifest, "mini")
    path = PACKS_ROOT / pack_id / "mini" / "assets.jsonl"
    data = path.read_bytes()
    checksum = _checksum_bytes(data)
    source = upsert_source_record(
        session,
        manifest,
        profile="mini",
        checksum=checksum,
        document_count=0,
        chunk_count=0,
        record_count=0,
        force=force,
    )
    count = 0
    for line in data.decode("utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        tenant_id = str(row["tenant_id"])
        estate_id = str(row["estate_id"])
        ensure_estate_scope(
            session, tenant_id=tenant_id, estate_id=estate_id, name=estate_id
        )
        session.add(
            FacilityAsset(
                id=uuid.uuid4().hex,
                tenant_id=tenant_id,
                estate_id=estate_id,
                property_name=row.get("property_name"),
                building=row.get("building"),
                floor=row.get("floor"),
                room_zone=row.get("room_zone"),
                asset_label=str(row["asset_label"]),
                manufacturer=row.get("manufacturer"),
                model=row.get("model"),
                serial=row.get("serial"),
                maintenance_interval_days=row.get("maintenance_interval_days"),
                work_order_id=row.get("work_order_id"),
                status=str(row.get("status") or "active"),
                cost=parse_money(row.get("cost")),
                labour_hours=parse_money(row.get("labour_hours")),
                inspection_result=row.get("inspection_result"),
                evaluation_only=True,
                meta={"evaluation_demo": True, "not_estate_truth": True},
            )
        )
        count += 1
    source.record_count = count
    return {"ok": True, "pack_id": pack_id, "asset_count": count}


def load_fleet_mini(session: Session, *, force: bool = False) -> Dict[str, Any]:
    pack_id = "steward_fleet_open_v1"
    manifest = load_manifest(pack_id)
    _assert_licence_ok(manifest, "mini")
    path = PACKS_ROOT / pack_id / "mini" / "vehicles.jsonl"
    data = path.read_bytes()
    checksum = _checksum_bytes(data)
    source = upsert_source_record(
        session,
        manifest,
        profile="mini",
        checksum=checksum,
        document_count=0,
        chunk_count=0,
        record_count=0,
        force=force,
    )
    count = 0
    for line in data.decode("utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        tenant_id = str(row["tenant_id"])
        estate_id = str(row["estate_id"])
        ensure_estate_scope(
            session, tenant_id=tenant_id, estate_id=estate_id, name=estate_id
        )
        session.add(
            FleetVehicle(
                id=uuid.uuid4().hex,
                tenant_id=tenant_id,
                estate_id=estate_id,
                vin_or_internal_id=str(row["vin_or_internal_id"]),
                registration=row.get("registration"),
                make=row.get("make"),
                model=row.get("model"),
                year=row.get("year"),
                driver_assignment=row.get("driver_assignment"),
                odometer_km=row.get("odometer_km"),
                next_service_km=row.get("next_service_km"),
                next_service_due=row.get("next_service_due"),
                defect_severity=row.get("defect_severity"),
                availability_status=str(row.get("availability_status") or "available"),
                evaluation_only=True,
                meta={"evaluation_demo": True, "not_estate_truth": True},
            )
        )
        count += 1
    source.record_count = count
    return {"ok": True, "pack_id": pack_id, "vehicle_count": count}


def load_full_or_fail(pack_id: str) -> None:
    """Full profile requires validated licence + HTTPS acquisition — fail closed."""
    manifest = load_manifest(pack_id)
    _assert_licence_ok(manifest, "full")
    url = manifest.get("resolved_download_url") or manifest.get("original_url")
    if not url or not str(url).startswith("https://"):
        raise RuntimeError(f"full profile requires HTTPS acquisition URL for {pack_id}")
    # Full downloaders are pack-specific and intentionally not auto-run in CI.
    raise RuntimeError(
        f"full loader for {pack_id} requires operator credentials / approved acquisition; "
        "mini profile is the committed CI path"
    )
