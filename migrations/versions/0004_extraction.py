"""Phase 1C: document extractions; EXTRACTING/EXTRACTED job statuses

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-06
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

OLD_ACTIVE = "status IN ('QUEUED', 'COLLECTING', 'COLLECTED', 'READING')"
NEW_ACTIVE = "status IN ('QUEUED', 'COLLECTING', 'COLLECTED', 'READING', 'EXTRACTING')"


def _active_index(where: str) -> None:
    op.drop_index("uq_active_job_per_account", table_name="verification_jobs")
    op.create_index("uq_active_job_per_account", "verification_jobs", ["account_id"],
                    unique=True, postgresql_where=sa.text(where), sqlite_where=sa.text(where))


def upgrade() -> None:
    _active_index(NEW_ACTIVE)
    op.create_table(
        "document_extractions",
        sa.Column("sha256", sa.String(64), sa.ForeignKey("stored_files.sha256"), primary_key=True),
        sa.Column("extraction_version", sa.String(160), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("expected_type", sa.String(32)),
        sa.Column("model_type", sa.String(32)),
        sa.Column("document_type", sa.String(32)),
        sa.Column("fields", sa.JSON()),
        sa.Column("issues", sa.JSON()),
        sa.Column("validity_status", sa.String(16)),
        sa.Column("valid_until", sa.String(10)),
        sa.Column("model", sa.String(80)),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False),
        sa.Column("seconds", sa.Double()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("document_extractions")
    _active_index(OLD_ACTIVE)
