from delphi_jobs.migrate import migrate

EXPECTED_TABLES = {
    "public": {"sports", "leagues", "seasons", "teams", "team_seasons", "team_aliases",
               "games", "game_external_ids", "results", "experts", "predictions"},
    "benchmark": {"market_lines"},
    "ops": {"schema_migrations"},
}


def test_all_tables_exist(db):
    rows = db.execute(
        """select table_schema, table_name from information_schema.tables
           where table_schema in ('public', 'benchmark', 'ops')"""
    ).fetchall()
    found: dict[str, set[str]] = {}
    for schema, table in rows:
        found.setdefault(schema, set()).add(table)
    for schema, tables in EXPECTED_TABLES.items():
        assert tables <= found.get(schema, set()), f"missing in {schema}: {tables - found.get(schema, set())}"


def test_migrate_is_idempotent(migrated_conn):
    assert migrate(migrated_conn) == []


def test_rls_enabled_on_every_public_table(db):
    rows = db.execute(
        """select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
           where n.nspname = 'public' and c.relkind = 'r' and not c.relrowsecurity"""
    ).fetchall()
    assert rows == []
