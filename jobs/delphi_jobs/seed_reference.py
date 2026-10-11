"""Seed reference data: sports, leagues, seasons, outlets, experts, and teams.

Safe to re-run. Every write is an upsert, and teams are matched by their
source ID (not their name), so renames update the existing team.

Usage:  python -m delphi_jobs.seed_reference [--season 2026]
"""

import argparse
import re
import sys
import unicodedata
from collections import defaultdict

import psycopg

from .db import connect

SPORTS = [{"slug": "football", "name": "Football"}]

LEAGUES = [
    {"slug": "nfl", "sport": "football", "name": "NFL"},
    {"slug": "cfb", "sport": "football", "name": "College Football"},
]

# Human experts are individual writers, created when their picks are first
# imported. Only the model and market experts are fixed reference data.
OUTLETS = [
    {"slug": "covers", "name": "Covers", "url": "https://www.covers.com"},
    {"slug": "sports-illustrated", "name": "Sports Illustrated", "url": "https://www.si.com"},
    {"slug": "cbs-sports", "name": "CBS Sports", "url": "https://www.cbssports.com"},
    {"slug": "yahoo-sports", "name": "Yahoo Sports", "url": "https://sports.yahoo.com"},
]

EXPERTS = [
    {"slug": "delphiedge", "name": "DelphiEdge", "kind": "model", "url": None},
    {"slug": "market", "name": "Market (closing line)", "kind": "market", "url": None},
]

# Which external source identifies teams in each league.
TEAM_ID_SOURCE = {"nfl": "nflverse", "cfb": "cfbd"}


def ascii_fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def alias_key(text: str) -> str:
    """Mirror of the public.alias_key SQL function."""
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower().replace("&", "").replace("'", "")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def seed_core(conn: psycopg.Connection, season: int) -> dict[str, int]:
    """Sports, leagues, seasons and experts. Returns league slug -> id."""
    for s in SPORTS:
        conn.execute(
            """insert into sports (slug, name) values (%(slug)s, %(name)s)
               on conflict (slug) do update set name = excluded.name""",
            s,
        )
    league_ids = {}
    for lg in LEAGUES:
        league_ids[lg["slug"]] = conn.execute(
            """insert into leagues (sport_id, slug, name)
               values ((select id from sports where slug = %(sport)s), %(slug)s, %(name)s)
               on conflict (slug) do update set name = excluded.name
               returning id""",
            lg,
        ).fetchone()[0]
        conn.execute(
            """insert into seasons (league_id, year, label) values (%s, %s, %s)
               on conflict (league_id, year) do nothing""",
            (league_ids[lg["slug"]], season, str(season)),
        )
    for o in OUTLETS:
        conn.execute(
            """insert into outlets (slug, name, url) values (%(slug)s, %(name)s, %(url)s)
               on conflict (slug) do update set name = excluded.name, url = excluded.url""",
            o,
        )
    for e in EXPERTS:
        conn.execute(
            """insert into experts (slug, name, kind, url)
               values (%(slug)s, %(name)s, %(kind)s, %(url)s)
               on conflict (slug) do update set
                 name = excluded.name, kind = excluded.kind, url = excluded.url""",
            e,
        )
    return league_ids


def upsert_team(conn: psycopg.Connection, league_id: int, id_source: str, team: dict) -> int:
    """Insert or update a team, matched on its external ID. Returns team id."""
    fields = {k: team[k] for k in
              ("name", "short_name", "abbreviation", "mascot", "color", "alt_color", "logo_url")}
    row = conn.execute(
        """select team_id from team_aliases
           where league_id = %s and source = %s and alias_key = public.alias_key(%s)""",
        (league_id, id_source, team["external_id"]),
    ).fetchone()
    if row:
        team_id = row[0]
        conn.execute(
            """update teams set name = %(name)s, short_name = %(short_name)s,
                 abbreviation = %(abbreviation)s, mascot = %(mascot)s, color = %(color)s,
                 alt_color = %(alt_color)s, logo_url = %(logo_url)s
               where id = %(id)s""",
            {**fields, "id": team_id},
        )
        return team_id
    slug = team["slug"] or slugify(team["name"])
    taken = conn.execute(
        "select 1 from teams where league_id = %s and slug = %s", (league_id, slug)
    ).fetchone()
    if taken:
        raise RuntimeError(
            f"Team slug '{slug}' already belongs to another team; refusing to merge "
            f"{id_source} id {team['external_id']} into it."
        )
    team_id = conn.execute(
        """insert into teams (league_id, slug, name, short_name, abbreviation, mascot,
                              color, alt_color, logo_url)
           values (%(league_id)s, %(slug)s, %(name)s, %(short_name)s, %(abbreviation)s,
                   %(mascot)s, %(color)s, %(alt_color)s, %(logo_url)s)
           returning id""",
        {**fields, "league_id": league_id, "slug": slug},
    ).fetchone()[0]
    conn.execute(
        "insert into team_aliases (team_id, league_id, source, alias) values (%s, %s, %s, %s)",
        (team_id, league_id, id_source, team["external_id"]),
    )
    return team_id


def add_name_aliases(conn: psycopg.Connection, league_id: int,
                     names_by_team: dict[int, list[str]]) -> list[str]:
    """Add human-readable aliases, plus an accent-free copy of each
    ('San José' also gets 'San Jose'). A name that would point at more than
    one team (e.g. 'New York', 'Los Angeles') is skipped. Returns skipped names."""
    owners: dict[str, set[int]] = defaultdict(set)
    display: dict[str, str] = {}
    for team_id, names in names_by_team.items():
        for name in names:
            for variant in (name.strip(), ascii_fold(name.strip())):
                key = alias_key(variant)
                if key:
                    owners[key].add(team_id)
                    display.setdefault(key, variant)
    skipped = sorted(display[k] for k, ids in owners.items() if len(ids) > 1)
    for key, ids in owners.items():
        if len(ids) != 1:
            continue
        (team_id,) = ids
        existing = conn.execute(
            """select team_id from team_aliases
               where league_id = %s and source = 'name' and alias_key = %s""",
            (league_id, key),
        ).fetchone()
        if existing is None:
            conn.execute(
                """insert into team_aliases (team_id, league_id, source, alias)
                   values (%s, %s, 'name', %s)""",
                (team_id, league_id, display[key]),
            )
        elif existing[0] != team_id:
            skipped.append(display[key])
    return skipped


def seed_teams(conn: psycopg.Connection, league_ids: dict[str, int], season: int,
               teams_by_league: dict[str, list[dict]]) -> dict[str, dict]:
    report = {}
    for league_slug, teams in teams_by_league.items():
        league_id = league_ids[league_slug]
        season_id = conn.execute(
            "select id from seasons where league_id = %s and year = %s", (league_id, season)
        ).fetchone()[0]
        names_by_team = {}
        for team in teams:
            team_id = upsert_team(conn, league_id, TEAM_ID_SOURCE[league_slug], team)
            conn.execute(
                """insert into team_seasons (team_id, season_id, conference, division, classification)
                   values (%s, %s, %s, %s, %s)
                   on conflict (team_id, season_id) do update set
                     conference = excluded.conference, division = excluded.division,
                     classification = excluded.classification""",
                (team_id, season_id, team["conference"], team["division"], team["classification"]),
            )
            names_by_team[team_id] = team["names"]
        skipped = add_name_aliases(conn, league_id, names_by_team)
        report[league_slug] = {"teams": len(teams), "ambiguous_names_skipped": skipped}
    return report


def run(conn: psycopg.Connection, season: int,
        teams_by_league: dict[str, list[dict]]) -> dict[str, dict]:
    league_ids = seed_core(conn, season)
    return seed_teams(conn, league_ids, season, teams_by_league)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--season", type=int, default=2026)
    args = parser.parse_args(argv)

    from .sources import cfbd, nflverse

    teams_by_league = {
        "nfl": nflverse.nfl_teams(args.season),
        "cfb": cfbd.fbs_teams(args.season),
    }
    with connect() as conn:
        with conn.transaction():
            report = run(conn, args.season, teams_by_league)
    for league, info in report.items():
        print(f"{league}: {info['teams']} teams seeded")
        if info["ambiguous_names_skipped"]:
            print(f"  ambiguous names not used as aliases: {', '.join(info['ambiguous_names_skipped'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
