"""Wave 2: audit ledger, heal approvals, decimal money columns

Revision ID: 0003_wave2_audit_heal_money
Revises: 0002_pilot_auth
Create Date: 2026-07-21
"""

from __future__ import annotations

from alembic import op

from app.steward.audit_models import AuditEvent, HealApproval

revision = "0003_wave2_audit_heal_money"
down_revision = "0002_pilot_auth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    AuditEvent.__table__.create(bind=bind, checkfirst=True)
    HealApproval.__table__.create(bind=bind, checkfirst=True)
    # Convert legacy float money columns to NUMERIC(20,4) when present.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'facility_assets' AND column_name = 'cost'
            ) THEN
                ALTER TABLE facility_assets
                    ALTER COLUMN cost TYPE NUMERIC(20,4) USING cost::numeric(20,4),
                    ALTER COLUMN labour_hours TYPE NUMERIC(20,4) USING labour_hours::numeric(20,4);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    for table in (HealApproval.__table__, AuditEvent.__table__):
        table.drop(bind=bind, checkfirst=True)
