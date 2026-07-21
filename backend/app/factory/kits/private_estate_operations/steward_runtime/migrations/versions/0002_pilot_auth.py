"""Pilot auth tables (PilotPrincipal, PilotAccessToken, EstateAccess)

Revision ID: 0002_pilot_auth
Revises: 0001_steward_rag
Create Date: 2026-07-21
"""

from __future__ import annotations

from alembic import op

from app.steward.auth_models import EstateAccess, PilotAccessToken, PilotPrincipal
from app.steward.models import Base

revision = "0002_pilot_auth"
down_revision = "0001_steward_rag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    PilotPrincipal.__table__.create(bind=bind, checkfirst=True)
    PilotAccessToken.__table__.create(bind=bind, checkfirst=True)
    EstateAccess.__table__.create(bind=bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in (EstateAccess.__table__, PilotAccessToken.__table__, PilotPrincipal.__table__):
        table.drop(bind=bind, checkfirst=True)
