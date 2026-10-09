"""Keep every other labelled item the AI reads from a document

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-08
"""
import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("document_extractions") as batch:
        batch.add_column(sa.Column("other_fields", sa.JSON()))


def downgrade() -> None:
    with op.batch_alter_table("document_extractions") as batch:
        batch.drop_column("other_fields")
