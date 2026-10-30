"""Notifications sent to Zoho Cliq / Google Sheets (never twice)

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-30
"""
import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("ref", sa.String(300), nullable=False),
        sa.Column("account_id", sa.String(32)),
        sa.Column("payload", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("kind", "ref", name="uq_notification_kind_ref"),
    )
    op.create_index("ix_notifications_account_id", "notifications", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_notifications_account_id", table_name="notifications")
    op.drop_table("notifications")
