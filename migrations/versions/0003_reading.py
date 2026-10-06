"""Phase 1B: file readings and pages; READING/READ job statuses

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-06
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

OLD_ACTIVE = "status IN ('QUEUED', 'COLLECTING')"
NEW_ACTIVE = "status IN ('QUEUED', 'COLLECTING', 'COLLECTED', 'READING')"


def _active_index(where: str) -> None:
    op.drop_index("uq_active_job_per_account", table_name="verification_jobs")
    op.create_index("uq_active_job_per_account", "verification_jobs", ["account_id"],
                    unique=True, postgresql_where=sa.text(where), sqlite_where=sa.text(where))


def upgrade() -> None:
    _active_index(NEW_ACTIVE)
    op.create_table(
        "file_readings",
        sa.Column("sha256", sa.String(64), sa.ForeignKey("stored_files.sha256"), primary_key=True),
        sa.Column("pipeline_version", sa.String(32), primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("document_type", sa.String(32)),
        sa.Column("type_confidence", sa.Double()),
        sa.Column("seconds", sa.Double()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "file_pages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("sha256", sa.String(64), sa.ForeignKey("stored_files.sha256"), nullable=False),
        sa.Column("pipeline_version", sa.String(32), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("text_method", sa.String(16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("grounding_text", sa.Text(), nullable=False),
        sa.Column("ocr_conf", sa.Double(), nullable=False),
        sa.Column("word_count", sa.Integer(), nullable=False),
        sa.Column("quality_label", sa.String(16), nullable=False),
        sa.Column("quality_score", sa.Double(), nullable=False),
        sa.Column("quality_reasons", sa.JSON()),
        sa.Column("rotation", sa.Integer(), nullable=False),
        sa.Column("cropped", sa.Boolean(), nullable=False),
        sa.Column("skew", sa.Double(), nullable=False),
        sa.Column("document_type", sa.String(32), nullable=False),
        sa.Column("type_confidence", sa.Double(), nullable=False),
        sa.Column("type_signals", sa.JSON()),
        sa.Column("image_key", sa.String(512), nullable=False),
        sa.Column("seconds", sa.Double(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("sha256", "pipeline_version", "page_number", name="uq_file_page"),
    )
    op.create_index("ix_file_pages_sha256", "file_pages", ["sha256"])


def downgrade() -> None:
    op.drop_table("file_pages")
    op.drop_table("file_readings")
    _active_index(OLD_ACTIVE)
