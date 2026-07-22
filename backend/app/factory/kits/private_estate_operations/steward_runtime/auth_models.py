"""Pilot authentication and estate authorization models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.steward.models import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PilotPrincipal(Base):
    __tablename__ = "pilot_principals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(255))
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(32), default="pilot")  # pilot|admin|curator
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class PilotAccessToken(Base):
    __tablename__ = "pilot_access_tokens"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    principal_id: Mapped[str] = mapped_column(
        ForeignKey("pilot_principals.id"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    label: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class EstateAccess(Base):
    __tablename__ = "estate_access"
    __table_args__ = (
        UniqueConstraint(
            "principal_id", "tenant_id", "estate_id", name="uq_estate_access_scope"
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    principal_id: Mapped[str] = mapped_column(
        ForeignKey("pilot_principals.id"), index=True
    )
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    estate_id: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(32), default="viewer")  # viewer|curator|admin
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
