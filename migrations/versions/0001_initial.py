"""Phase 1A: jobs, stored files, documents, audit events

Revision ID: 0001
Revises:
Create Date: 2026-10-05
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

ACTIVE = "status IN ('QUEUED', 'COLLECTING')"


def upgrade() -> None:
    op.create_table(
        "verification_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("account_id", sa.String(32), nullable=False),
        sa.Column("customer_number", sa.String(64)),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("requested_by", sa.String(255)),
        sa.Column("source", sa.String(32), nullable=False, server_default="crm_button"),
        sa.Column("reported_document_status", sa.String(8)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("documents_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_verification_jobs_account_id", "verification_jobs", ["account_id"])
    op.create_index("ix_verification_jobs_status", "verification_jobs", ["status"])
    op.create_index(
        "uq_active_job_per_account", "verification_jobs", ["account_id"], unique=True,
        postgresql_where=sa.text(ACTIVE), sqlite_where=sa.text(ACTIVE),
    )

    op.create_table(
        "stored_files",
        sa.Column("sha256", sa.String(64), primary_key=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("mime_type", sa.String(128), nullable=False),
        sa.Column("extension", sa.String(16), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(),
                  sa.ForeignKey("verification_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_field", sa.String(64)),
        sa.Column("zoho_parent_id", sa.String(32)),
        sa.Column("zoho_file_ref", sa.String(255), nullable=False),
        sa.Column("file_name", sa.String(512)),
        sa.Column("zoho_created_time", sa.String(40)),
        sa.Column("zoho_uploaded_by", sa.String(255)),
        sa.Column("sha256", sa.String(64), sa.ForeignKey("stored_files.sha256")),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("mime_type", sa.String(128)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("status_detail", sa.Text()),
        sa.Column("duplicate_of_id", sa.Uuid(), sa.ForeignKey("documents.id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("job_id", "source", "zoho_file_ref", name="uq_document_source_ref"),
    )
    op.create_index("ix_documents_job_id", "documents", ["job_id"])
    op.create_index("ix_documents_sha256", "documents", ["sha256"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("verification_jobs.id", ondelete="CASCADE")),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False, server_default=""),
        sa.Column("payload", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_events_job_id", "audit_events", ["job_id"])


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_table("documents")
    op.drop_table("stored_files")
    op.drop_table("verification_jobs")
