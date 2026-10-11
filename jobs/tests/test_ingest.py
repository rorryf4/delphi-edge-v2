"""Game, score and closing-line ingestion."""

from datetime import date, datetime, timedelta, timezone

import psycopg
import pytest

from delphi_jobs.ingest_games import ingest
from delphi_jobs.sources.cfbd import cfb_game_records
from delphi_jobs.sources.nflverse import nfl_game_records
from factories import add_prediction, seed

NOW = datetime(2026, 10, 11, 18, 0, tzinfo=timezone.utc)


def nfl_row(game_id="2026_05_NYG_KC", home="KC", away="NYG", gameday="2026-10-04",
            gametime="13:00", home_score=None, away_score=None, spread_line=3.5, **kw):
    return {"game_id": game_id, "game_type": "REG", "week": 5, "gameday": gameday,
            "gametime": gametime, "home_team": home, "away_team": away,
            "home_score": home_score, "away_score": away_score, "overtime": 0,
            "location": "Home", "stadium": "Arrowhead Stadium", "spread_line": spread_line,
            "total_line": 44.5, "home_moneyline": -180, "away_moneyline": 150, **kw}


def cfb_game(gid=401, home_id=251, home="Texas", away_id=2534, away="Sam Houston FCS",
             away_class="fcs", completed=True, home_pts=45, away_pts=10, tbd=False,
             start="2026-09-06T23:30:00.000Z", linescores=(7, 14, 10, 14)):
    return {"id": gid, "seasonType": "regular", "week": 2, "startDate": start,
            "startTimeTBD": tbd, "completed": completed, "neutralSite": False,
            "venue": "DKR", "homeId": home_id, "homeTeam": home, "homeClassification": "fbs",
            "homeConference": "SEC", "homePoints": home_pts if completed else None,
            "homeLineScores": list(linescores) if completed else None,
            "awayId": away_id, "awayTeam": away, "awayClassification": away_class,
            "awayConference": "Southland", "awayPoints": away_pts if completed else None}


def cfb_lines(gid=401, spread=-28.5):
    return {"id": gid, "lines": [
        {"provider": "DraftKings", "spread": spread, "overUnder": 55.5,
         "homeMoneyline": -5000, "awayMoneyline": 1800},
        {"provider": "Bovada", "spread": None, "overUnder": None,
         "homeMoneyline": None, "awayMoneyline": None},
    ]}


@pytest.fixture
def seeded(db):
    seed(db)
    return db


def game_row(conn, source, ext):
    return conn.execute(
        """select g.id, g.status, g.starts_at, g.start_time_tbd, h.slug, a.slug, g.week
           from games g join game_external_ids x on x.game_id = g.id
           join teams h on h.id = g.home_team_id join teams a on a.id = g.away_team_id
           where x.source = %s and x.external_id = %s""", (source, ext)).fetchone()


# ------------------------------------------------------------ nflverse ---
def test_nfl_kickoff_is_converted_from_eastern_time():
    (rec,) = nfl_game_records([nfl_row(gameday="2026-10-04", gametime="13:00")])
    assert rec["starts_at"] == datetime(2026, 10, 4, 17, 0, tzinfo=timezone.utc)  # EDT
    (rec,) = nfl_game_records([nfl_row(gameday="2026-12-06", gametime="13:00")])
    assert rec["starts_at"] == datetime(2026, 12, 6, 18, 0, tzinfo=timezone.utc)  # EST


def test_nfl_spread_sign_is_flipped_to_sportsbook_convention():
    (rec,) = nfl_game_records([nfl_row(spread_line=3.5)])  # home favored by 3.5
    assert rec["lines"][0]["home_spread"] == -3.5
    (rec,) = nfl_game_records([nfl_row(spread_line=0.0)])
    assert rec["lines"][0]["home_spread"] == 0.0


def test_nfl_ingest_inserts_games_results_and_closing_lines(seeded):
    rows = [nfl_row(home_score=27, away_score=20),
            nfl_row(game_id="2026_07_KC_NYJ", home="NYJ", away="KC", gameday="2026-10-18")]
    counts = ingest(seeded, "nfl", 2026, nfl_game_records(rows), now=NOW)
    assert counts["games_inserted"] == 2
    assert counts["results_written"] == 1
    assert counts["closing_lines_written"] == 1  # only the game that has kicked off
    gid, status, *_ = game_row(seeded, "nflverse", "2026_05_NYG_KC")
    assert status == "final"
    assert seeded.execute("select home_score, away_score, overtime from results where game_id = %s",
                          (gid,)).fetchone() == (27, 20, False)
    assert seeded.execute(
        "select provider, is_closing, home_spread, total from benchmark.market_lines where game_id = %s",
        (gid,)).fetchone() == ("nflverse", True, -3.5, 44.5)
    assert seeded.execute("select starts_on, ends_on from seasons s join leagues l "
                          "on l.id = s.league_id where l.slug = 'nfl'").fetchone() == (
        date(2026, 10, 4), date(2026, 10, 18))


def test_nfl_ingest_is_idempotent(seeded):
    recs = nfl_game_records([nfl_row(home_score=27, away_score=20)])
    ingest(seeded, "nfl", 2026, recs, now=NOW)
    again = ingest(seeded, "nfl", 2026, recs, now=NOW)
    assert (again["games_inserted"], again["games_updated"], again["results_written"],
            again["closing_lines_written"]) == (0, 0, 0, 0)


def test_score_correction_and_schedule_change_are_applied(seeded):
    ingest(seeded, "nfl", 2026, nfl_game_records([nfl_row(home_score=27, away_score=20)]), now=NOW)
    counts = ingest(seeded, "nfl", 2026, nfl_game_records(
        [nfl_row(home_score=27, away_score=23, gametime="16:25")]), now=NOW)
    assert counts["games_updated"] == 1 and counts["results_written"] == 1
    gid, _, starts_at, *_ = game_row(seeded, "nflverse", "2026_05_NYG_KC")
    assert starts_at == datetime(2026, 10, 4, 20, 25, tzinfo=timezone.utc)
    assert seeded.execute("select away_score from results where game_id = %s", (gid,)).fetchone() == (23,)


def test_unknown_nfl_team_is_an_error(seeded):
    with pytest.raises(RuntimeError, match="Unknown nfl team 'XYZ'"):
        with seeded.transaction():
            ingest(seeded, "nfl", 2026, nfl_game_records([nfl_row(away="XYZ")]), now=NOW)


def test_duplicate_source_game_is_an_error(seeded):
    recs = nfl_game_records([nfl_row(), nfl_row()])
    with pytest.raises(RuntimeError, match="Duplicate game"):
        with seeded.transaction():
            ingest(seeded, "nfl", 2026, recs, now=NOW)


# ---------------------------------------------------------------- cfbd ---
def test_cfb_ingest_creates_fcs_opponent_and_stores_lines(seeded):
    recs = cfb_game_records([cfb_game()], [cfb_lines()])
    counts = ingest(seeded, "cfb", 2026, recs, now=NOW)
    assert counts["teams_created"] == 1
    gid, status, _, _, home, away, week = game_row(seeded, "cfbd", "401")
    assert (status, home, away, week) == ("final", "texas", "sam-houston-fcs", 2)
    assert seeded.execute(
        """select ts.classification, ts.conference from team_seasons ts
           join teams t on t.id = ts.team_id where t.slug = 'sam-houston-fcs'""").fetchone() == (
        "fcs", "Southland")
    lines = seeded.execute("select provider, home_spread from benchmark.market_lines "
                           "where game_id = %s", (gid,)).fetchall()
    assert lines == [("cfbd:draftkings", -28.5)]  # Bovada had no values


def test_cfb_overtime_is_detected_from_line_scores():
    (rec,) = cfb_game_records([cfb_game(linescores=(7, 7, 7, 7, 3))], [])
    assert rec["overtime"] is True


def test_lines_are_not_stored_before_kickoff_or_while_time_is_tbd(seeded):
    future = cfb_game(gid=402, completed=False, start="2026-10-17T19:30:00.000Z")
    tbd = cfb_game(gid=403, completed=False, tbd=True, start="2026-10-11T04:00:00.000Z")
    counts = ingest(seeded, "cfb", 2026,
                    cfb_game_records([future, tbd], [cfb_lines(402), cfb_lines(403)]), now=NOW)
    assert counts["closing_lines_written"] == 0


def test_closing_line_updates_in_place(seeded):
    ingest(seeded, "cfb", 2026, cfb_game_records([cfb_game()], [cfb_lines(spread=-28.5)]), now=NOW)
    counts = ingest(seeded, "cfb", 2026, cfb_game_records([cfb_game()], [cfb_lines(spread=-27.5)]),
                    now=NOW)
    assert counts["closing_lines_written"] == 1
    assert seeded.execute("select count(*), max(home_spread) from benchmark.market_lines").fetchone() == (
        1, -27.5)


def test_tbd_game_does_not_lock_picks_until_time_is_set(seeded):
    """A TBD game's start is a midnight placeholder; picks made that day must
    still be accepted, and the lock applies once a real time is known."""
    placeholder = cfb_game(gid=404, completed=False, tbd=True, start="2026-10-11T04:00:00.000Z")
    ingest(seeded, "cfb", 2026, cfb_game_records([placeholder], []), now=NOW)
    gid = game_row(seeded, "cfbd", "404")[0]
    game = {"id": gid, "kickoff": datetime(2026, 10, 11, 4, 0, tzinfo=timezone.utc)}
    home = seeded.execute("select home_team_id from games where id = %s", (gid,)).fetchone()[0]
    add_prediction(seeded, game, pick_team_id=home,
                   published_at=datetime(2026, 10, 11, 15, 0, tzinfo=timezone.utc))

    timed = cfb_game(gid=404, completed=False, start="2026-10-11T16:00:00.000Z")
    ingest(seeded, "cfb", 2026, cfb_game_records([timed], []), now=NOW)
    with pytest.raises(psycopg.errors.RaiseException, match="locked"):
        with seeded.transaction():
            seeded.execute("update predictions set notes = 'x', pick_team_id = away_team_id "
                           "from games where games.id = predictions.game_id and games.id = %s", (gid,))
