"""``INSERT … ON CONFLICT`` that works on Postgres and SQLite (idempotent writers, SPEC §6.2)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Table
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Insert


def insert_ignore(session: AsyncSession, table: Table, rows: Sequence[dict[str, Any]]) -> Insert:
    dialect = session.get_bind().dialect.name
    factory = postgresql.insert if dialect == "postgresql" else sqlite.insert
    return factory(table).values(list(rows)).on_conflict_do_nothing()
