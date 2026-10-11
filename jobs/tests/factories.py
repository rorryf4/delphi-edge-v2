"""Small builders for test data."""

from datetime import datetime, timedelta, timezone

from delphi_jobs import seed_reference


def team(ext_id: str, name: str, names: list[str] | None = None, slug: str | None = None,
         conference: str | None = None) -> dict:
    return {
        "external_id": ext_id, "slug": slug, "name": name, "short_name": name,
        "abbreviation": ext_id, "mascot": None, "color": None, "alt_color": None,
        "logo_url": None, "conference": conference, "division": None,
        "classification": None, "names": names or [name],
    }


NFL = [
    team("NYG", "New York Giants", ["New York Giants", "Giants", "New York", "NYG"], slug="nyg"),
    team("NYJ", "New York Jets", ["New York Jets", "Jets", "New York", "NYJ"], slug="nyj"),
    team("KC", "Kansas City Chiefs", ["Kansas City Chiefs", "Chiefs", "Kansas City", "KC"], slug="kc"),
]
CFB = [
    team("251", "Texas", ["Texas", "UT", "Texas Longhorns"], conference="SEC"),
    team("245", "Texas A&M", ["Texas A&M", "TAMU"], conference="SEC"),
]


def seed(conn, season: int = 2026):
    return seed_reference.run(conn, season, {"nfl": NFL, "cfb": CFB})


def now() -> datetime:
    return datetime.now(timezone.utc)


def make_game(conn, kickoff: datetime, home_slug: str = "kc", away_slug: str = "nyg") -> dict:
    row = conn.execute(
        """insert into games (season_id, season_type, week, starts_at, home_team_id, away_team_id)
           select s.id, 'regular', 1, %s, h.id, a.id
           from seasons s join leagues l on l.id = s.league_id
           join teams h on h.league_id = l.id and h.slug = %s
           join teams a on a.league_id = l.id and a.slug = %s
           where l.slug = 'nfl' and s.year = 2026
           returning id, home_team_id, away_team_id""",
        (kickoff, home_slug, away_slug),
    ).fetchone()
    return {"id": row[0], "home": row[1], "away": row[2], "kickoff": kickoff}


def expert_id(conn, slug: str = "test-writer") -> int:
    """Id of a human expert, created on first use."""
    row = conn.execute("select id from experts where slug = %s", (slug,)).fetchone()
    if row:
        return row[0]
    return conn.execute(
        """insert into experts (slug, name, kind, outlet_id)
           values (%s, 'Test Writer', 'human', (select id from outlets where slug = 'covers'))
           returning id""",
        (slug,),
    ).fetchone()[0]


def add_prediction(conn, game: dict, **fields) -> int:
    values = {
        "expert_id": expert_id(conn),
        "game_id": game["id"],
        "published_at": game["kickoff"] - timedelta(days=1),
        "source": "test",
        **fields,
    }
    cols = ", ".join(values)
    params = ", ".join(f"%({c})s" for c in values)
    return conn.execute(
        f"insert into predictions ({cols}) values ({params}) returning id", values
    ).fetchone()[0]
