"""Database connections. Jobs talk to Postgres directly (not through the
Supabase REST API) so they can use transactions and upserts."""

import psycopg

from .config import require


def connect(url: str | None = None) -> psycopg.Connection:
    # prepare_threshold=None keeps us compatible with Supabase's connection pooler.
    return psycopg.connect(url or require("DATABASE_URL"), prepare_threshold=None)
