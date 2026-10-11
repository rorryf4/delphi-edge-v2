"""Apply SQL migrations in db/migrations in filename order, once each.

Usage:  python -m delphi_jobs.migrate          apply pending migrations
        python -m delphi_jobs.migrate --status list applied and pending
"""

import sys
from pathlib import Path

import psycopg

from .config import MIGRATIONS_DIR
from .db import connect

TRACKING_DDL = """
create schema if not exists ops;
create table if not exists ops.schema_migrations (
  version     text primary key,
  applied_at  timestamptz not null default now()
);
"""


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    files = sorted(directory.glob("*.sql"))
    if not files:
        # An empty list would make every check pass silently. This happens if
        # the package is installed somewhere other than the repo checkout.
        raise RuntimeError(f"No migrations found in {directory}. Install with: pip install -e ./jobs")
    return files


def applied_versions(conn: psycopg.Connection) -> set[str]:
    conn.execute(TRACKING_DDL)
    return {row[0] for row in conn.execute("select version from ops.schema_migrations")}


def migrate(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply each pending migration in its own transaction. Returns versions applied."""
    with conn.transaction():
        done = applied_versions(conn)
    applied = []
    for path in migration_files(directory):
        version = path.stem
        if version in done:
            continue
        with conn.transaction():
            conn.execute(path.read_text())
            conn.execute("insert into ops.schema_migrations (version) values (%s)", (version,))
        applied.append(version)
    return applied


def main(argv: list[str]) -> int:
    with connect() as conn:
        if "--status" in argv:
            with conn.transaction():
                done = applied_versions(conn)
            for path in migration_files():
                print(("applied  " if path.stem in done else "PENDING  ") + path.stem)
            return 0
        applied = migrate(conn)
    print("Applied: " + ", ".join(applied) if applied else "Database is up to date.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
