"""Phase 1D: job assessments (cross-document checks, Zoho proposals, recommendation)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-07
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_assessments",
        sa.Column("job_id", sa.Uuid(),
                  sa.ForeignKey("verification_jobs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("rules_version", sa.String(40), nullable=False),
        sa.Column("recommendation", sa.String(20), nullable=False),
        sa.Column("assessment", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("job_assessments")
