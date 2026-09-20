"""dedup signatures and cluster stage

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "post_signatures",
        sa.Column("post_id", sa.BigInteger(), nullable=False),
        sa.Column("text_key", sa.Text(), nullable=True),
        sa.Column("text_hash", sa.String(length=40), nullable=True),
        sa.Column("file_sha256", sa.String(length=64), nullable=True),
        sa.Column("phash", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["post_id"], ["posts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("post_id"),
    )
    op.create_index("ix_post_signatures_text_hash", "post_signatures", ["text_hash"])
    op.create_index("ix_post_signatures_file_sha256", "post_signatures", ["file_sha256"])
    op.add_column("post_clusters", sa.Column("stage", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("post_clusters", "stage")
    op.drop_index("ix_post_signatures_file_sha256", table_name="post_signatures")
    op.drop_index("ix_post_signatures_text_hash", table_name="post_signatures")
    op.drop_table("post_signatures")
