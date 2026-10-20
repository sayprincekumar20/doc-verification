"""Keep the Account / Attachments / Notes sent by the Verify button until collection

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-20
"""
import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("verification_jobs") as batch:
        batch.add_column(sa.Column("button_data", sa.JSON()))


def downgrade() -> None:
    with op.batch_alter_table("verification_jobs") as batch:
        batch.drop_column("button_data")
