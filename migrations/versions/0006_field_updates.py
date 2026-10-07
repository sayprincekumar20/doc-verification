"""Automatic Zoho updates: audit trail

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-07
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "field_updates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(),
                  sa.ForeignKey("verification_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_id", sa.String(32), nullable=False),
        sa.Column("zoho_field", sa.String(80), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("old_value", sa.Text()),
        sa.Column("new_value", sa.Text()),
        sa.Column("confidence", sa.Double()),
        sa.Column("grounding", sa.String(16)),
        sa.Column("sources", sa.JSON()),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("detail", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_field_updates_job_id", "field_updates", ["job_id"])
    op.create_index("ix_field_updates_account_id", "field_updates", ["account_id"])


def downgrade() -> None:
    op.drop_table("field_updates")
