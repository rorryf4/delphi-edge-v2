"""Tests run against a throwaway local Postgres (see scripts/test.sh).

They refuse to run against anything that is not on localhost, so they can
never touch the Supabase database."""

import os
from urllib.parse import urlparse

import psycopg
import pytest

from delphi_jobs.config import REPO_ROOT
from delphi_jobs.migrate import migrate

ROLES_SQL = REPO_ROOT / "db" / "test" / "supabase_roles.sql"


def _test_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.exit("TEST_DATABASE_URL is not set. Run tests with scripts/test.sh.", returncode=2)
    if urlparse(url).hostname not in ("localhost", "127.0.0.1", "::1"):
        pytest.exit("TEST_DATABASE_URL must point at localhost.", returncode=2)
    return url


@pytest.fixture(scope="session")
def migrated_conn():
    with psycopg.connect(_test_url(), prepare_threshold=None) as conn:
        with conn.transaction():
            conn.execute(ROLES_SQL.read_text())
        migrate(conn)
        yield conn


@pytest.fixture
def db(migrated_conn):
    """A connection whose changes are rolled back after each test."""
    with migrated_conn.transaction(force_rollback=True):
        yield migrated_conn
