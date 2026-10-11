"""Grading: straight up, against the spread, margin error, Brier, and the
no-vig market benchmark. Includes pushes, ties and missing data."""

from datetime import timedelta

import pytest

from delphi_jobs.grade import (grade_prediction, implied_probability, market_prediction,
                               no_vig_home_probability, run)
from factories import add_prediction, make_game, now, seed

HOME, AWAY = 1, 2


def pred(**kw):
    base = {"home_team_id": HOME, "away_team_id": AWAY, "pick_team_id": None,
            "ats_pick_team_id": None, "quoted_home_spread": None, "predicted_margin": None,
            "home_win_prob": None}
    return {**base, **kw}


# ----------------------------------------------------------------- math ---
def test_implied_probability():
    assert implied_probability(-110) == pytest.approx(0.52381, abs=1e-5)
    assert implied_probability(150) == pytest.approx(0.4)
    assert implied_probability(100) == pytest.approx(0.5)


def test_vig_is_removed():
    assert no_vig_home_probability(-110, -110) == pytest.approx(0.5)
    # -200 / +170: 0.6667 and 0.3704 sum to 1.037; normalized home = 0.6429
    assert no_vig_home_probability(-200, 170) == pytest.approx(0.642857, abs=1e-5)
    assert no_vig_home_probability(-200, None) is None


# ----------------------------------------------------------- straight up ---
@pytest.mark.parametrize("pick, score, expected", [
    (HOME, (24, 17), "win"), (AWAY, (24, 17), "loss"), (AWAY, (10, 13), "win"),
    (HOME, (20, 20), "push"), (None, (24, 17), None),
])
def test_straight_up(pick, score, expected):
    assert grade_prediction(pred(pick_team_id=pick), *score, None)["su_result"] == expected


# ---------------------------------------------------- against the spread ---
@pytest.mark.parametrize("ats_pick, quoted, closing, score, result, line, source", [
    (HOME, -3.5, None, (24, 20), "win", -3.5, "quoted"),    # home -3.5 wins by 4
    (HOME, -3.5, None, (23, 20), "loss", -3.5, "quoted"),   # wins by 3, no cover
    (AWAY, -3.5, None, (23, 20), "win", -3.5, "quoted"),    # away +3.5 covers
    (HOME, -3.0, None, (23, 20), "push", -3.0, "quoted"),   # exact: push
    (AWAY, 3.0, None, (20, 23), "push", 3.0, "quoted"),     # away -3 wins by 3: push
    (HOME, None, -7.0, (30, 20), "win", -7.0, "closing"),   # no quote: closing line
    (HOME, -1.0, -7.0, (23, 20), "win", -1.0, "quoted"),    # quote beats closing
    (AWAY, None, 0.0, (17, 17), "push", 0.0, "closing"),    # pick'em tie
])
def test_against_the_spread(ats_pick, quoted, closing, score, result, line, source):
    g = grade_prediction(pred(ats_pick_team_id=ats_pick, quoted_home_spread=quoted), *score, closing)
    assert (g["ats_result"], g["ats_line"], g["ats_line_source"]) == (result, line, source)


def test_ats_without_any_line_is_ungraded_with_reason():
    g = grade_prediction(pred(ats_pick_team_id=HOME), 24, 17, None)
    assert g["ats_result"] is None and g["ats_line"] is None
    assert "no quoted or closing line" in g["ungraded_reason"]


# --------------------------------------------------- margin and Brier ---
def test_margin_error():
    assert grade_prediction(pred(predicted_margin=3.0), 27, 20, None)["margin_error"] == 4.0
    assert grade_prediction(pred(predicted_margin=-3.5), 27, 20, None)["margin_error"] == 10.5
    assert grade_prediction(pred(), 27, 20, None)["margin_error"] is None


def test_brier():
    assert grade_prediction(pred(home_win_prob=0.7), 27, 20, None)["brier"] == pytest.approx(0.09)
    assert grade_prediction(pred(home_win_prob=0.7), 20, 27, None)["brier"] == pytest.approx(0.49)
    assert grade_prediction(pred(), 27, 20, None)["brier"] is None


def test_tie_is_not_brier_scored_but_other_metrics_are():
    g = grade_prediction(pred(home_win_prob=0.6, pick_team_id=HOME, predicted_margin=3.0),
                         20, 20, None)
    assert g["brier"] is None and "tied" in g["ungraded_reason"]
    assert g["su_result"] == "push" and g["margin_error"] == 3.0


# ------------------------------------------------------ market benchmark ---
def line(spread=-3.5, total=44.5, hml=-180, aml=150, sp="nflverse", mp="nflverse"):
    return {"home_spread": spread, "total": total, "home_moneyline": hml,
            "away_moneyline": aml, "spread_provider": sp, "ml_provider": mp}


def test_market_prediction_uses_no_vig_moneyline_and_spread():
    m = market_prediction(line(), HOME, AWAY)
    assert m["pick_team_id"] == HOME
    # -180 / +150: 0.6429 and 0.4000 sum to 1.0429; normalized home = 0.6164
    assert m["home_win_prob"] == pytest.approx(0.6164, abs=1e-4)
    assert m["predicted_margin"] == 3.5 and m["predicted_total"] == 44.5
    assert m["source"] == "market:nflverse"


def test_market_falls_back_to_spread_without_moneylines():
    m = market_prediction(line(spread=2.5, hml=None, aml=None, mp=None), HOME, AWAY)
    assert (m["pick_team_id"], m["home_win_prob"], m["predicted_margin"]) == (AWAY, None, -2.5)


def test_market_pickem_has_no_straight_up_pick():
    m = market_prediction(line(spread=0.0, hml=-110, aml=-110), HOME, AWAY)
    assert m["pick_team_id"] is None and m["home_win_prob"] == 0.5


def test_market_with_no_usable_line_makes_no_prediction():
    assert market_prediction(line(spread=None, total=50, hml=None, aml=None, sp=None, mp=None),
                             HOME, AWAY) is None


# ------------------------------------------------------------- database ---
def final_game(db, home_score, away_score, hours_ago=5, home="kc", away="nyg"):
    game = make_game(db, now() - timedelta(hours=hours_ago), home, away)
    db.execute("update games set status = 'final' where id = %s", (game["id"],))
    db.execute("insert into results (game_id, home_score, away_score) values (%s, %s, %s)",
               (game["id"], home_score, away_score))
    return game


def add_line(db, game, provider, spread=None, total=None, hml=None, aml=None):
    db.execute("""insert into benchmark.market_lines (game_id, provider, is_closing, home_spread,
                    total, home_moneyline, away_moneyline) values (%s, %s, true, %s, %s, %s, %s)""",
               (game["id"], provider, spread, total, hml, aml))


def board(db, slug):
    return db.execute(
        """select games_graded, su_wins, su_losses, su_pushes, ats_wins, ats_losses, ats_pushes,
                  margin_graded, margin_mae, brier_graded, brier
           from leaderboard where expert_slug = %s""", (slug,)).fetchone()


@pytest.fixture
def seeded(db):
    seed(db)
    return db


def test_end_to_end_grading_and_leaderboard(seeded):
    db = seeded
    g1 = final_game(db, 27, 20, hours_ago=30)                    # KC wins by 7
    g2 = final_game(db, 20, 20, hours_ago=20, home="nyj")        # tie
    g3 = final_game(db, 17, 24, hours_ago=10, home="nyg", away="kc")  # KC (away) wins by 7
    add_line(db, g1, "nflverse", spread=-3.0, total=45, hml=-160, aml=140)
    add_line(db, g2, "nflverse", spread=-1.0, hml=-115, aml=-105)
    # g3 has no closing line at all: missing data.

    add_prediction(db, g1, pick_team_id=g1["home"], ats_pick_team_id=g1["home"],
                   predicted_home_score=24, predicted_away_score=20)       # quote: none
    add_prediction(db, g2, pick_team_id=g2["home"], ats_pick_team_id=g2["away"])
    add_prediction(db, g3, ats_pick_team_id=g3["away"], quoted_home_spread=7.0)  # push
    counts = run(db)
    assert counts["market_predictions_changed"] == 2
    assert counts["predictions_on_final_games"] == 5

    # writer: SU 1-0-1, ATS g1 win (7 - 3 > 0), g2 win (tie, +1 covers), g3 push
    assert board(db, "test-writer") == (3, 1, 0, 1, 2, 0, 1, 1, 3.0, 0, None)
    # market: g1 KC fav wins, g2 tie: SU push, no brier
    games, w, l, push, aw, al, ap, mn, mae, bn, brier = board(db, "market")
    assert (games, w, l, push, aw, al, ap, mn, bn) == (2, 1, 0, 1, 0, 0, 0, 2, 1)
    assert float(mae) == pytest.approx((4 + 1) / 2)
    p_home = round(no_vig_home_probability(-160, 140), 4)   # 0.5963
    assert float(brier) == pytest.approx((1 - p_home) ** 2, abs=1e-4)

    again = run(db)
    assert again["market_predictions_changed"] == 0 and again["grades_changed"] == 0


def test_score_correction_regrades(seeded):
    db = seeded
    g = final_game(db, 27, 20)
    add_prediction(db, g, pick_team_id=g["home"])
    run(db)
    db.execute("update results set away_score = 30 where game_id = %s", (g["id"],))
    assert run(db)["grades_changed"] == 1
    assert board(db, "test-writer")[1:3] == (0, 1)


def test_only_final_games_are_graded(seeded):
    db = seeded
    g = make_game(db, now() - timedelta(hours=1))   # kicked off, not final
    add_line(db, g, "nflverse", spread=-3.0, hml=-150, aml=130)
    add_prediction(db, g, pick_team_id=g["home"])
    counts = run(db)
    assert counts["predictions_on_final_games"] == 0
    assert counts["market_predictions_changed"] == 1   # derived, graded once final


def test_closing_line_change_updates_market_after_kickoff(seeded):
    db = seeded
    g = final_game(db, 27, 20)
    add_line(db, g, "nflverse", spread=-3.0, hml=-150, aml=130)
    run(db)
    db.execute("update benchmark.market_lines set home_spread = -4.0 where game_id = %s", (g["id"],))
    assert run(db)["market_predictions_changed"] == 1
    assert db.execute("""select p.predicted_margin from predictions p join experts e
                         on e.id = p.expert_id where e.slug = 'market'""").fetchone() == (4.0,)
    # The lock override is scoped: a normal edit afterwards is still refused.
    import psycopg
    with pytest.raises(psycopg.errors.RaiseException, match="locked"):
        with db.transaction():
            db.execute("update predictions set pick_team_id = away_team_id from games "
                       "where games.id = predictions.game_id")


def test_cfb_provider_preference(seeded):
    db = seeded
    game = db.execute(
        """insert into games (season_id, season_type, week, starts_at, status, home_team_id, away_team_id)
           select s.id, 'regular', 1, now() - interval '5 hours', 'final', h.id, a.id
           from seasons s join leagues l on l.id = s.league_id
           join teams h on h.league_id = l.id and h.slug = 'texas'
           join teams a on a.league_id = l.id and a.slug = 'texas-am'
           where l.slug = 'cfb' returning id""").fetchone()[0]
    db.execute("insert into results values (%s, 31, 30)", (game,))
    g = {"id": game}
    add_line(db, g, "cfbd:bovada", spread=-9.0, hml=-400, aml=300)
    add_line(db, g, "cfbd:draftkings", spread=-7.5)        # preferred, but no moneyline
    run(db)
    margin, source = db.execute("""select p.predicted_margin, p.source from predictions p
        join experts e on e.id = p.expert_id where e.slug = 'market'""").fetchone()
    assert float(margin) == 7.5                      # spread from DraftKings
    assert source == "market:cfbd:bovada+cfbd:draftkings"   # moneyline from Bovada


def test_anon_can_read_grades_and_leaderboard(seeded):
    db = seeded
    g = final_game(db, 27, 20)
    add_prediction(db, g, pick_team_id=g["home"])
    run(db)
    db.execute("set local role anon")
    assert db.execute("select count(*) from grades").fetchone() == (1,)
    assert db.execute("select su_wins from leaderboard").fetchone() == (1,)
    db.execute("reset role")


# ---------------------------------------------------------- head to head ---
def h2h(db, a, b, league=None):
    cols = ("expert_slug", "shared_games", "su_graded", "su_wins", "su_losses", "su_pushes",
            "ats_graded", "margin_graded", "margin_mae", "brier_graded")
    rows = db.execute(f"select {', '.join(cols)} from head_to_head(%s, %s, %s)",
                      (a, b, league)).fetchall()
    return [dict(zip(cols, r)) for r in rows]


def test_head_to_head_uses_only_shared_games_per_metric(seeded):
    from factories import expert_id
    db = seeded
    g1 = final_game(db, 27, 20, hours_ago=30)
    g2 = final_game(db, 24, 21, hours_ago=20, home="nyj")
    g3 = final_game(db, 10, 31, hours_ago=10, home="nyg", away="kc")
    for g in (g1, g2, g3):
        add_line(db, g, "nflverse", spread=-3.0, hml=-150, aml=130)
    # Writer A: all three games; scores only on g1.
    add_prediction(db, g1, pick_team_id=g1["home"], predicted_home_score=30, predicted_away_score=20)
    add_prediction(db, g2, pick_team_id=g2["away"])
    add_prediction(db, g3, pick_team_id=g3["away"])
    # Writer B: only g2 and g3, with scores on both.
    b = expert_id(db, "writer-b")
    add_prediction(db, g2, expert_id=b, pick_team_id=g2["home"],
                   predicted_home_score=27, predicted_away_score=20)
    add_prediction(db, g3, expert_id=b, pick_team_id=g3["home"],
                   predicted_home_score=24, predicted_away_score=21)
    run(db)

    a_row, b_row = h2h(db, "test-writer", "writer-b")
    assert a_row["expert_slug"] == "test-writer" and b_row["expert_slug"] == "writer-b"
    # g1 is excluded: B has no pick there.
    assert a_row["shared_games"] == b_row["shared_games"] == 2
    assert (a_row["su_graded"], a_row["su_wins"], a_row["su_losses"]) == (2, 1, 1)
    assert (b_row["su_graded"], b_row["su_wins"], b_row["su_losses"]) == (2, 1, 1)
    # Margin: A has no scores on g2 or g3, so there is nothing shared to compare.
    assert a_row["margin_graded"] == b_row["margin_graded"] == 0
    assert a_row["margin_mae"] is None and b_row["margin_mae"] is None

    m_row, a2 = h2h(db, "market", "test-writer")
    assert m_row["shared_games"] == 3
    assert m_row["margin_graded"] == a2["margin_graded"] == 1    # only g1 has both
    assert m_row["brier_graded"] == a2["brier_graded"] == 0      # writer gives no probability


def test_head_to_head_with_no_shared_games(seeded):
    rows = h2h(seeded, "market", "nobody")
    assert [r["shared_games"] for r in rows] == [0, 0]
    assert [r["su_graded"] for r in rows] == [0, 0]
