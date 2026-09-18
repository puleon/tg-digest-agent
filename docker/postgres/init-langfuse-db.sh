#!/usr/bin/env bash
# Runs once on first Postgres start: Langfuse gets its own database on the shared instance.
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-SQL
    CREATE DATABASE langfuse;
SQL
