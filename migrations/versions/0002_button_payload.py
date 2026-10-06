"""Store the full CRM button payload, the Account snapshot and job warnings

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("verification_jobs") as batch:
        batch.drop_column("requested_by")
        batch.add_column(sa.Column("reported_attachment_count", sa.Integer()))
        batch.add_column(sa.Column("requested_by_user_id", sa.String(32)))
        batch.add_column(sa.Column("requested_by_email", sa.String(255)))
        batch.add_column(sa.Column("requested_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("account_snapshot", sa.JSON()))
        batch.add_column(sa.Column("warnings", sa.JSON()))


def downgrade() -> None:
    with op.batch_alter_table("verification_jobs") as batch:
        batch.drop_column("warnings")
        batch.drop_column("account_snapshot")
        batch.drop_column("requested_at")
        batch.drop_column("requested_by_email")
        batch.drop_column("requested_by_user_id")
        batch.drop_column("reported_attachment_count")
        batch.add_column(sa.Column("requested_by", sa.String(255)))
