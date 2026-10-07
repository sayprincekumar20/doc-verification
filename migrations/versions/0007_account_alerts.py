"""Alerts (Zoho Note + Task) for missing/expired/problem documents

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-07
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_alerts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(),
                  sa.ForeignKey("verification_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("account_id", sa.String(32), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("alerts", sa.JSON(), nullable=False),
        sa.Column("status_decision", sa.String(20), nullable=False),
        sa.Column("note_status", sa.String(20), nullable=False),
        sa.Column("task_status", sa.String(20), nullable=False),
        sa.Column("zoho_note_id", sa.String(32)),
        sa.Column("zoho_task_id", sa.String(32)),
        sa.Column("detail", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_account_alerts_job_id", "account_alerts", ["job_id"])
    op.create_index("ix_account_alerts_account_id", "account_alerts", ["account_id"])


def downgrade() -> None:
    op.drop_table("account_alerts")
