"""Column types that behave on Postgres (production) and SQLite (unit tests)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, BigInteger, Integer
from sqlalchemy.dialects.postgresql import JSONB

# JSONB in Postgres, plain JSON elsewhere.
JSONType = JSON().with_variant(JSONB(), "postgresql")
JSONDict = dict[str, Any]

# SQLite only autoincrements INTEGER PRIMARY KEY, so BigInteger keys need a variant.
BigIntPK = BigInteger().with_variant(Integer(), "sqlite")
