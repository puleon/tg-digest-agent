"""digest trace id (feedback scores attach to the run that produced the issue)

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("digests", sa.Column("trace_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("digests", "trace_id")
