"""What the publishable key (Supabase's anon role) can and cannot do."""

from datetime import timedelta

import psycopg
import pytest

from factories import add_prediction, make_game, now, seed


@pytest.fixture
def as_anon(db):
    seed(db)
    game = make_game(db, now() + timedelta(days=2))
    add_prediction(db, game, pick_team_id=game["home"])
    db.execute("insert into benchmark.market_lines (game_id, provider, is_closing, home_spread) "
               "values (%s, 'nflverse', true, -3.5)", (game["id"],))
    db.execute("set local role anon")
    return db


@pytest.mark.parametrize("table", [
    "sports", "leagues", "seasons", "teams", "team_seasons",
    "games", "results", "experts", "predictions",
])
def test_anon_can_read_scorecard_tables(as_anon, table):
    as_anon.execute(f"select * from public.{table}").fetchall()


def test_anon_sees_predictions(as_anon):
    assert as_anon.execute("select count(*) from predictions").fetchone()[0] == 1


@pytest.mark.parametrize("table", ["team_aliases", "game_external_ids"])
def test_anon_sees_no_internal_rows(as_anon, table):
    assert as_anon.execute(f"select count(*) from public.{table}").fetchone()[0] == 0


def test_anon_cannot_read_market_lines(as_anon):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with as_anon.transaction():
            as_anon.execute("select * from benchmark.market_lines")


def test_anon_cannot_write(as_anon):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with as_anon.transaction():
            as_anon.execute("insert into sports (slug, name) values ('x', 'X')")
    # Row-level security makes updates and deletes match zero rows.
    assert as_anon.execute("update experts set name = 'hacked'").rowcount == 0
    assert as_anon.execute("delete from predictions").rowcount == 0
