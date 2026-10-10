"""NFL data from nflverse (public, no key required)."""

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
