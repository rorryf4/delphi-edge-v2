"""Scorecard integrity rules enforced by the database."""

from datetime import timedelta

import psycopg
import pytest

from factories import add_prediction, make_game, now, seed


@pytest.fixture
def seeded(db):
    seed(db)
    return db


def test_pick_must_be_a_team_in_the_game(seeded):
    game = make_game(seeded, now() + timedelta(days=2), "kc", "nyg")
    jets = seeded.execute("select id from teams where slug = 'nyj'").fetchone()[0]
    with pytest.raises(psycopg.errors.RaiseException, match="not in game"):
        with seeded.transaction():
            add_prediction(seeded, game, pick_team_id=jets)
    with pytest.raises(psycopg.errors.RaiseException, match="not in game"):
        with seeded.transaction():
            add_prediction(seeded, game, ats_pick_team_id=jets)


def test_prediction_must_be_published_before_kickoff(seeded):
    game = make_game(seeded, now() + timedelta(days=2))
    with pytest.raises(psycopg.errors.RaiseException, match="not before kickoff"):
        with seeded.transaction():
            add_prediction(seeded, game, pick_team_id=game["home"],
                           published_at=game["kickoff"] + timedelta(minutes=1))


def test_margin_and_total_are_derived_from_scores(seeded):
    game = make_game(seeded, now() + timedelta(days=2))
    pid = add_prediction(seeded, game, predicted_home_score=27, predicted_away_score=20)
    margin, total = seeded.execute(
        "select predicted_margin, predicted_total from predictions where id = %s", (pid,)
    ).fetchone()
    assert (margin, total) == (7, 47)


def test_inconsistent_margin_is_rejected(seeded):
    game = make_game(seeded, now() + timedelta(days=2))
    with pytest.raises(psycopg.errors.RaiseException, match="do not match"):
        with seeded.transaction():
            add_prediction(seeded, game, predicted_home_score=27, predicted_away_score=20,
                           predicted_margin=3)


def test_quoted_spread_requires_an_ats_pick(seeded):
    game = make_game(seeded, now() + timedelta(days=2))
    with pytest.raises(psycopg.errors.CheckViolation):
        with seeded.transaction():
            add_prediction(seeded, game, pick_team_id=game["home"], quoted_home_spread=-3.5)


def test_empty_prediction_is_rejected(seeded):
    game = make_game(seeded, now() + timedelta(days=2))
    with pytest.raises(psycopg.errors.CheckViolation):
        with seeded.transaction():
            add_prediction(seeded, game)


def test_one_prediction_per_expert_per_game(seeded):
    game = make_game(seeded, now() + timedelta(days=2))
    add_prediction(seeded, game, pick_team_id=game["home"])
    with pytest.raises(psycopg.errors.UniqueViolation):
        with seeded.transaction():
            add_prediction(seeded, game, pick_team_id=game["away"])


def test_prediction_can_change_before_kickoff(seeded):
    game = make_game(seeded, now() + timedelta(days=2))
    pid = add_prediction(seeded, game, pick_team_id=game["home"])
    seeded.execute("update predictions set pick_team_id = %s where id = %s", (game["away"], pid))
    seeded.execute("delete from predictions where id = %s", (pid,))


def test_prediction_is_locked_after_kickoff(seeded):
    game = make_game(seeded, now() - timedelta(hours=1))
    pid = add_prediction(seeded, game, pick_team_id=game["home"])
    with pytest.raises(psycopg.errors.RaiseException, match="locked"):
        with seeded.transaction():
            seeded.execute("update predictions set pick_team_id = %s where id = %s",
                           (game["away"], pid))
    with pytest.raises(psycopg.errors.RaiseException, match="locked"):
        with seeded.transaction():
            seeded.execute("delete from predictions where id = %s", (pid,))
    # Non-scoring fields such as notes can still be edited.
    seeded.execute("update predictions set notes = 'source checked' where id = %s", (pid,))


def test_locked_prediction_can_be_corrected_deliberately(seeded):
    game = make_game(seeded, now() - timedelta(hours=1))
    pid = add_prediction(seeded, game, pick_team_id=game["home"])
    with seeded.transaction():
        seeded.execute("set local delphi.allow_locked_edit = 'on'")
        seeded.execute("update predictions set pick_team_id = %s where id = %s",
                       (game["away"], pid))
    picked = seeded.execute("select pick_team_id from predictions where id = %s", (pid,)).fetchone()
    assert picked == (game["away"],)


def test_one_closing_line_per_provider(seeded):
    game = make_game(seeded, now() + timedelta(days=2))
    sql = ("insert into benchmark.market_lines (game_id, provider, is_closing, home_spread) "
           "values (%s, 'nflverse', true, %s)")
    seeded.execute(sql, (game["id"], -3.5))
    with pytest.raises(psycopg.errors.UniqueViolation):
        with seeded.transaction():
            seeded.execute(sql, (game["id"], -4.0))
