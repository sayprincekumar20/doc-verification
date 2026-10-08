"""Phase 3: link job assessments to their Zoho review record

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-08
"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("job_assessments") as batch:
        batch.add_column(sa.Column("zoho_review_id", sa.String(32)))
        batch.add_column(sa.Column("review_error", sa.Text()))


def downgrade() -> None:
    with op.batch_alter_table("job_assessments") as batch:
        batch.drop_column("review_error")
        batch.drop_column("zoho_review_id")
