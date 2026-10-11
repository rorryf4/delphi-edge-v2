"""CSV import of pundit picks."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from delphi_jobs.import_picks import COLUMNS, import_file
from factories import make_game, now, seed

ET = ZoneInfo("America/New_York")


@pytest.fixture
def setup(db):
    seed(db)
    kickoff = (now() + timedelta(days=3)).replace(second=0, microsecond=0)
    game = make_game(db, kickoff, home_slug="kc", away_slug="nyg")
    return db, game


def published(hours_ago: int = 2) -> str:
    return (now() - timedelta(hours=hours_ago)).astimezone(ET).strftime("%Y-%m-%d %H:%M")


def row(**kw) -> dict:
    base = {"league": "nfl", "outlet": "cbs-sports", "expert": "Sample Writer",
            "published_at": published(), "away_team": "Giants", "home_team": "Chiefs"}
    return {**base, **kw}


def write_csv(tmp_path, rows, columns=COLUMNS, name="picks.csv"):
    path = tmp_path / name
    lines = [",".join(columns)]
    for r in rows:
        lines.append(",".join(str(r.get(c, "")) for c in columns))
    path.write_text("\n".join(lines) + "\n")
    return path


def prediction(conn):
    return conn.execute(
        """select e.slug, o.slug, p.pick_team_id, p.ats_pick_team_id, p.quoted_home_spread,
                  p.predicted_home_score, p.predicted_away_score, p.predicted_margin, p.source
           from predictions p join experts e on e.id = p.expert_id
           join outlets o on o.id = e.outlet_id""").fetchall()


def test_template_header_matches_importer():
    from delphi_jobs.config import REPO_ROOT
    header = (REPO_ROOT / "data" / "picks" / "template.csv").read_text().strip().split(",")
    assert header == COLUMNS


def test_full_row_imports_and_creates_writer(tmp_path, setup):
    db, game = setup
    path = write_csv(tmp_path, [row(pick="Chiefs", ats_pick="Giants", ats_line="+3.5",
                                    away_score=20, home_score=23, source_url="https://x.test/a")])
    counts, errors = import_file(db, path)
    assert errors == []
    assert counts == {"inserted": 1, "updated": 0, "unchanged": 0, "experts_created": 1}
    (p,) = prediction(db)
    # Giants +3.5 means the home Chiefs are -3.5.
    assert p == ("sample-writer", "cbs-sports", game["home"], game["away"], -3.5, 23, 20, 3,
                 "csv:picks.csv")


def test_home_ats_line_is_stored_as_given(tmp_path, setup):
    db, game = setup
    path = write_csv(tmp_path, [row(ats_pick="KC", ats_line="-6.5")])
    assert import_file(db, path)[1] == []
    assert prediction(db)[0][4] == -6.5


def test_pick_is_derived_from_predicted_score(tmp_path, setup):
    db, game = setup
    path = write_csv(tmp_path, [row(away_score=27, home_score=24)])
    assert import_file(db, path)[1] == []
    assert prediction(db)[0][2] == game["away"]


def test_reimport_changes_nothing_and_edit_updates(tmp_path, setup):
    db, game = setup
    path = write_csv(tmp_path, [row(pick="Chiefs")])
    import_file(db, path)
    counts, _ = import_file(db, path)
    assert counts["unchanged"] == 1 and counts["inserted"] == 0
    path = write_csv(tmp_path, [row(pick="Giants")])
    counts, _ = import_file(db, path)
    assert counts["updated"] == 1
    assert prediction(db)[0][2] == game["away"]


def test_dry_run_saves_nothing(tmp_path, setup):
    db, _ = setup
    counts, errors = import_file(db, write_csv(tmp_path, [row(pick="Chiefs")]), dry_run=True)
    assert errors == [] and counts["inserted"] == 1
    assert prediction(db) == []
    assert db.execute("select count(*) from experts where slug = 'sample-writer'").fetchone() == (0,)


def test_any_bad_row_blocks_the_whole_file(tmp_path, setup):
    db, _ = setup
    path = write_csv(tmp_path, [
        row(pick="Chiefs"),                                          # row 2 ok
        row(expert="B", pick="Cheifs"),                              # 3 typo
        row(expert="C", away_team="Chiefs", home_team="Giants", pick="Chiefs"),  # 4 swapped
        row(expert="D", ats_line="-3"),                              # 5 line without pick
        row(expert="E", pick="Giants", away_score=10, home_score=20),  # 6 disagrees
        row(expert="F", outlet="ESPN", pick="Chiefs"),               # 7 unknown outlet
        row(expert="G", published_at="2026-10-14", pick="Chiefs"),   # 8 no time
        row(expert="H", published_at=(now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M"),
            pick="Chiefs"),                                          # 9 future
        row(pick="Giants"),                                          # 10 duplicate of 2
        row(expert="I", pick="Jets"),                                # 11 not in game
        row(expert="J"),                                             # 12 nothing picked
        row(expert="K", ats_pick="Chiefs", ats_line="-3.25"),        # 13 quarter point
        row(expert="DelphiEdge", pick="Chiefs"),                     # 14 reserved
    ])
    counts, errors = import_file(db, path)
    assert counts is None
    text = "\n".join(errors)
    for expected in [
        "row 3: pick 'Cheifs' does not match a team. Did you mean: Chiefs?",
        "row 4: home and away are swapped: the schedule has New York Giants at Kansas City Chiefs",
        "row 5: ats_line needs an ats_pick",
        "row 6: pick disagrees with the predicted score",
        "row 7: outlet 'ESPN' is not a known outlet",
        "row 8: published_at '2026-10-14' needs a time",
        "row 9: published_at is in the future",
        "row 10: same expert and game as row 2",
        "row 11: pick 'Jets' is not playing in this game",
        "row 12: row has no pick, ats_pick or predicted score",
        "row 13: ats_line -3.25 must be a whole or half point",
        "row 14: expert 'DelphiEdge' is reserved",
    ]:
        assert expected in text, f"missing: {expected}\n--- got:\n{text}"
    assert len(errors) == 12
    assert prediction(db) == []


def test_pick_after_kickoff_is_rejected(tmp_path, db):
    seed(db)
    kickoff = now() - timedelta(hours=1)
    make_game(db, kickoff)
    late = (kickoff + timedelta(minutes=5)).astimezone(ET).strftime("%Y-%m-%d %H:%M")
    _, errors = import_file(db, write_csv(tmp_path, [row(published_at=late, pick="Chiefs")]))
    assert len(errors) == 1 and "not before kickoff" in errors[0]


def test_locked_pick_cannot_be_changed_by_reimport(tmp_path, db):
    seed(db)
    kickoff = (now() - timedelta(hours=1)).replace(second=0, microsecond=0)
    make_game(db, kickoff)
    early = (kickoff - timedelta(days=1)).astimezone(ET).strftime("%Y-%m-%d %H:%M")
    assert import_file(db, write_csv(tmp_path, [row(published_at=early, pick="Chiefs")]))[1] == []
    _, errors = import_file(db, write_csv(tmp_path, [row(published_at=early, pick="Giants")]))
    assert len(errors) == 1 and "locked" in errors[0]


def test_writer_cannot_switch_outlets_silently(tmp_path, setup):
    db, _ = setup
    import_file(db, write_csv(tmp_path, [row(pick="Chiefs")]))
    _, errors = import_file(db, write_csv(tmp_path, [row(outlet="Covers", pick="Chiefs")]))
    assert "already exists under a different outlet" in errors[0]


def test_bad_header_is_reported(tmp_path, setup):
    db, _ = setup
    path = write_csv(tmp_path, [row(pick="Chiefs")], columns=[*COLUMNS[:-1], "comment"])
    _, errors = import_file(db, path)
    assert "unknown columns: comment" in errors[0]


def test_outlet_can_be_written_as_name(tmp_path, setup):
    db, _ = setup
    path = write_csv(tmp_path, [row(outlet="CBS Sports", pick="KC")])
    assert import_file(db, path)[1] == []
