"""Ingest schedules, final scores and closing lines for a season.

Every run re-reads the whole season from the source, so the first run is the
backfill and later runs pick up new finals, score corrections and schedule
changes. Safe to re-run.

Common game format produced by delphi_jobs.sources.*:
    external_id, season_type, week, starts_at (UTC), start_time_tbd,
    home / away: {"external_id", ...team fields used to create unknown teams},
    neutral_site, venue, status ('final' | 'scheduled'),
    home_score, away_score, overtime,
    lines: [{"provider", "home_spread", "total", "home_moneyline", "away_moneyline"}]

Sportsbook lines go to benchmark.market_lines (a grading benchmark, never a
model input) and only once a game has kicked off, so what is stored is the
closing line.

Usage:  scripts/job delphi_jobs.ingest_games [--season 2026] [--league nfl cfb]
"""

import argparse
import sys
from collections import Counter
from datetime import datetime, timezone

import psycopg

from .db import connect
from .seed_reference import TEAM_ID_SOURCE, add_name_aliases, upsert_team

# Leagues where an unknown opponent may be created on the fly (e.g. FCS
# teams playing FBS teams). In the NFL an unknown team means bad data.
CREATE_UNKNOWN_TEAMS = {"cfb"}

GAME_COLUMNS = ("season_id", "season_type", "week", "starts_at", "start_time_tbd",
                "home_team_id", "away_team_id", "neutral_site", "venue", "status")


class Ingest:
    def __init__(self, conn: psycopg.Connection, league_slug: str, season: int, now: datetime):
        self.conn = conn
        self.league_slug = league_slug
        self.source = TEAM_ID_SOURCE[league_slug]
        self.now = now
        self.counts: Counter = Counter()
        self.season_teams: set[int] = set()
        row = conn.execute(
            """select l.id, s.id from leagues l join seasons s on s.league_id = l.id
               where l.slug = %s and s.year = %s""",
            (league_slug, season),
        ).fetchone()
        if row is None:
            raise RuntimeError(f"No {league_slug} {season} season. Run seed_reference first.")
        self.league_id, self.season_id = row
        self.team_ids: dict[str, int] = {
            ext: tid for ext, tid in conn.execute(
                "select alias, team_id from team_aliases where league_id = %s and source = %s",
                (self.league_id, self.source),
            ).fetchall()
        }

    # ----------------------------------------------------------- teams ---
    def team_id(self, team: dict) -> int:
        ext = team["external_id"]
        team_id = self.team_ids.get(ext)
        if team_id is None:
            if self.league_slug not in CREATE_UNKNOWN_TEAMS:
                raise RuntimeError(
                    f"Unknown {self.league_slug} team {ext!r}. Run seed_reference first.")
            team_id = upsert_team(self.conn, self.league_id, self.source, team)
            add_name_aliases(self.conn, self.league_id,
                             {team_id: team.get("names") or [team["name"]]})
            self.team_ids[ext] = team_id
            self.counts["teams_created"] += 1
        if team_id not in self.season_teams:
            # Seeded teams already have a row for the season; this covers
            # opponents created here, including in later seasons.
            self.conn.execute(
                """insert into team_seasons (team_id, season_id, conference, division, classification)
                   values (%s, %s, %s, %s, %s) on conflict (team_id, season_id) do nothing""",
                (team_id, self.season_id, team.get("conference"), team.get("division"),
                 team.get("classification")),
            )
            self.season_teams.add(team_id)
        return team_id

    # ----------------------------------------------------------- games ---
    def game(self, rec: dict) -> None:
        values = {
            "season_id": self.season_id,
            "season_type": rec["season_type"],
            "week": rec["week"],
            "starts_at": rec["starts_at"],
            "start_time_tbd": rec["start_time_tbd"],
            "home_team_id": self.team_id(rec["home"]),
            "away_team_id": self.team_id(rec["away"]),
            "neutral_site": rec["neutral_site"],
            "venue": rec["venue"],
            "status": rec["status"],
        }
        row = self.conn.execute(
            "select game_id from game_external_ids where source = %s and external_id = %s",
            (self.source, rec["external_id"]),
        ).fetchone()
        if row is None:
            cols = ", ".join(GAME_COLUMNS)
            params = ", ".join(f"%({c})s" for c in GAME_COLUMNS)
            game_id = self.conn.execute(
                f"insert into games ({cols}) values ({params}) returning id", values
            ).fetchone()[0]
            self.conn.execute(
                "insert into game_external_ids (game_id, source, external_id) values (%s, %s, %s)",
                (game_id, self.source, rec["external_id"]),
            )
            self.counts["games_inserted"] += 1
        else:
            game_id = row[0]
            sets = ", ".join(f"{c} = %({c})s" for c in GAME_COLUMNS)
            current = ", ".join(GAME_COLUMNS)
            wanted = ", ".join(f"%({c})s::{t}" for c, t in zip(GAME_COLUMNS, (
                "integer", "text", "smallint", "timestamptz", "boolean",
                "integer", "integer", "boolean", "text", "text")))
            updated = self.conn.execute(
                f"""update games set {sets}
                    where id = %(id)s and ({current}) is distinct from ({wanted})""",
                {**values, "id": game_id},
            ).rowcount
            self.counts["games_updated"] += updated

        if rec["status"] == "final":
            self.result(game_id, rec)
        if rec["lines"] and not rec["start_time_tbd"] and rec["starts_at"] <= self.now:
            for line in rec["lines"]:
                self.closing_line(game_id, line)

    def result(self, game_id: int, rec: dict) -> None:
        changed = self.conn.execute(
            """insert into results (game_id, home_score, away_score, overtime)
               values (%s, %s, %s, %s)
               on conflict (game_id) do update set
                 home_score = excluded.home_score, away_score = excluded.away_score,
                 overtime = excluded.overtime
               where (results.home_score, results.away_score, results.overtime)
                     is distinct from (excluded.home_score, excluded.away_score, excluded.overtime)""",
            (game_id, rec["home_score"], rec["away_score"], rec["overtime"]),
        ).rowcount
        self.counts["results_written"] += changed

    def closing_line(self, game_id: int, line: dict) -> None:
        changed = self.conn.execute(
            """insert into benchmark.market_lines
                 (game_id, provider, is_closing, home_spread, total,
                  home_moneyline, away_moneyline, captured_at)
               values (%(game_id)s, %(provider)s, true, %(home_spread)s, %(total)s,
                       %(home_moneyline)s, %(away_moneyline)s, %(now)s)
               on conflict (game_id, provider) where is_closing do update set
                 home_spread = excluded.home_spread, total = excluded.total,
                 home_moneyline = excluded.home_moneyline,
                 away_moneyline = excluded.away_moneyline,
                 captured_at = excluded.captured_at
               where (market_lines.home_spread, market_lines.total,
                      market_lines.home_moneyline, market_lines.away_moneyline)
                     is distinct from
                     (excluded.home_spread, excluded.total,
                      excluded.home_moneyline, excluded.away_moneyline)""",
            {**line, "game_id": game_id, "now": self.now},
        ).rowcount
        self.counts["closing_lines_written"] += changed

    def season_dates(self) -> None:
        self.conn.execute(
            """update seasons set
                 starts_on = sub.first_day, ends_on = sub.last_day
               from (select min((starts_at at time zone 'America/New_York')::date) first_day,
                            max((starts_at at time zone 'America/New_York')::date) last_day
                     from games where season_id = %(sid)s) sub
               where seasons.id = %(sid)s and sub.first_day is not null""",
            {"sid": self.season_id},
        )


def ingest(conn: psycopg.Connection, league_slug: str, season: int, records: list[dict],
           now: datetime | None = None) -> Counter:
    job = Ingest(conn, league_slug, season, now or datetime.now(timezone.utc))
    seen = set()
    for rec in records:
        if rec["external_id"] in seen:
            raise RuntimeError(f"Duplicate game {rec['external_id']} in source data")
        seen.add(rec["external_id"])
        job.game(rec)
    job.season_dates()
    job.counts["games_in_source"] = len(records)
    return job.counts


def fetch(league_slug: str, season: int) -> list[dict]:
    from .sources import cfbd, nflverse
    return {"nfl": nflverse.nfl_games, "cfb": cfbd.cfb_games}[league_slug](season)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--league", nargs="+", choices=sorted(TEAM_ID_SOURCE), default=["nfl", "cfb"])
    args = parser.parse_args(argv)
    with connect() as conn:
        for league in args.league:
            records = fetch(league, args.season)
            with conn.transaction():
                counts = ingest(conn, league, args.season, records)
            summary = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
            print(f"{league} {args.season}: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
