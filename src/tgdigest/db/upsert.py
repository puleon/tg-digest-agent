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


def insert_or_update(
    session: AsyncSession,
    table: Table,
    rows: Sequence[dict[str, Any]],
    *,
    index_elements: Sequence[str],
    update_columns: Sequence[str],
) -> Insert:
    dialect = session.get_bind().dialect.name
    factory = postgresql.insert if dialect == "postgresql" else sqlite.insert
    stmt = factory(table).values(list(rows))
    return stmt.on_conflict_do_update(
        index_elements=list(index_elements),
        set_={c: getattr(stmt.excluded, c) for c in update_columns},
    )
