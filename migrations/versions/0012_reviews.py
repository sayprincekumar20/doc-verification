"""Reviews done in the Accounts dashboard (engine review API)

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-31
"""
import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("number", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("verification_jobs.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("account_id", sa.String(32), nullable=False),
        sa.Column("account_name", sa.String(255)),
        sa.Column("customer_number", sa.String(64)),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("recommendation", sa.String(20), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("rows", sa.JSON(), nullable=False),
        sa.Column("pages", sa.JSON(), nullable=False),
        sa.Column("requested_by", sa.String(255)),
        sa.Column("reviewer", sa.String(255)),
        sa.Column("reviewer_notes", sa.Text()),
        sa.Column("values_compared", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("activate_account", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("activated", sa.Boolean()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("images_purged", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_reviews_account_id", "reviews", ["account_id"])
    op.create_index("ix_reviews_status", "reviews", ["status"])


def downgrade() -> None:
    op.drop_index("ix_reviews_status", table_name="reviews")
    op.drop_index("ix_reviews_account_id", table_name="reviews")
    op.drop_table("reviews")
