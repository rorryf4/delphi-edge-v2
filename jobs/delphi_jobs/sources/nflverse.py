"""NFL data from nflverse (public, no key required)."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import nflreadpy as nfl


def nfl_teams(season: int) -> list[dict]:
    """Teams on the given season's schedule. The nflverse team table also
    lists relocated franchises (e.g. STL), so filter to active teams."""
    schedule = nfl.load_schedules([season])
    active = set(schedule["home_team"].to_list()) | set(schedule["away_team"].to_list())
    teams = []
    for row in nfl.load_teams().iter_rows(named=True):
        if row["team_abbr"] not in active:
            continue
        nick = row["team_nick"]
        full = row["team_name"]
        city = full[: -len(nick)].strip() if full.endswith(nick) else None
        teams.append(
            {
                "external_id": row["team_abbr"],
                "slug": row["team_abbr"].lower(),
                "name": full,
                "short_name": nick,
                "abbreviation": row["team_abbr"],
                "mascot": nick,
                "color": row["team_color"],
                "alt_color": row["team_color2"],
                "logo_url": row["team_logo_espn"],
                "conference": row["team_conf"],
                "division": row["team_division"],
                "classification": None,
                "names": [n for n in (full, nick, city, row["team_abbr"]) if n],
            }
        )
    if len(teams) != len(active):
        missing = active - {t["external_id"] for t in teams}
        raise RuntimeError(f"nflverse team table is missing scheduled teams: {sorted(missing)}")
    return teams


EASTERN = ZoneInfo("America/New_York")


def nfl_games(season: int) -> list[dict]:
    return nfl_game_records(nfl.load_schedules([season]).iter_rows(named=True))


def nfl_game_records(rows) -> list[dict]:
    """Convert nflverse schedule rows to the common game format
    (see delphi_jobs.ingest_games)."""
    records = []
    for r in rows:
        final = r["home_score"] is not None and r["away_score"] is not None
        kickoff = datetime.strptime(f"{r['gameday']} {r['gametime']}", "%Y-%m-%d %H:%M")
        lines = []
        if any(r[k] is not None for k in ("spread_line", "total_line", "home_moneyline")):
            lines.append({
                "provider": "nflverse",
                # nflverse spread_line is positive when the home team is favored;
                # we store sportsbook style (negative = home favored).
                "home_spread": None if r["spread_line"] is None else (-r["spread_line"] or 0.0),
                "total": r["total_line"],
                "home_moneyline": r["home_moneyline"],
                "away_moneyline": r["away_moneyline"],
            })
        records.append({
            "external_id": r["game_id"],
            "season_type": "regular" if r["game_type"] == "REG" else "postseason",
            "week": r["week"],
            "starts_at": kickoff.replace(tzinfo=EASTERN).astimezone(timezone.utc),
            "start_time_tbd": False,
            "home": {"external_id": r["home_team"]},
            "away": {"external_id": r["away_team"]},
            "neutral_site": r["location"] == "Neutral",
            "venue": r["stadium"],
            "status": "final" if final else "scheduled",
            "home_score": r["home_score"] if final else None,
            "away_score": r["away_score"] if final else None,
            "overtime": bool(r["overtime"]) if final and r["overtime"] is not None else None,
            "lines": lines,
        })
    return records
