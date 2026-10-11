"""College football data from CollegeFootballData.com (free API key)."""

from datetime import datetime

import httpx

from ..config import require

BASE_URL = "https://api.collegefootballdata.com"


def client() -> httpx.Client:
    return httpx.Client(
        base_url=BASE_URL,
        headers={"Authorization": f"Bearer {require('CFBD_API_KEY')}"},
        timeout=30,
    )


def fbs_teams(year: int) -> list[dict]:
    with client() as http:
        response = http.get("/teams/fbs", params={"year": year})
        response.raise_for_status()
        rows = response.json()
    teams = []
    for row in rows:
        school = row["school"]
        mascot = row.get("mascot")
        names = [school, *(row.get("alternateNames") or [])]
        if row.get("abbreviation"):
            names.append(row["abbreviation"])
        if mascot:
            names.append(f"{school} {mascot}")
        teams.append(
            {
                "external_id": str(row["id"]),
                "slug": None,  # derived from the school name by the seed
                "name": school,
                "short_name": school,
                "abbreviation": row.get("abbreviation"),
                "mascot": mascot,
                "color": row.get("color"),
                "alt_color": row.get("alternateColor"),
                "logo_url": (row.get("logos") or [None])[0],
                "conference": row.get("conference"),
                "division": row.get("division"),
                "classification": row.get("classification") or "fbs",
                "names": names,
            }
        )
    return teams


SEASON_TYPES = ("regular", "postseason")


def cfb_games(year: int) -> list[dict]:
    """All games involving an FBS team, regular season and postseason, with
    each provider's line. Two API calls per run: the free tier allows 1,000
    calls a month, and the schedule runs this about 170 times a month."""
    params = {"year": year, "seasonType": "both"}
    with client() as http:
        response = http.get("/games", params={**params, "classification": "fbs"})
        response.raise_for_status()
        games = response.json()
        response = http.get("/lines", params=params)
        response.raise_for_status()
        lines = response.json()
    return cfb_game_records(games, lines)


def _provider_slug(name: str) -> str:
    return "cfbd:" + "".join(ch for ch in name.lower() if ch.isalnum())


def cfb_game_records(games: list[dict], lines: list[dict]) -> list[dict]:
    """Convert CFBD games and lines to the common game format
    (see delphi_jobs.ingest_games). CFBD spreads are already sportsbook
    style from the home team's view (negative = home favored)."""
    lines_by_game = {row["id"]: row.get("lines") or [] for row in lines}
    records = []
    for g in games:
        final = bool(g["completed"]) and g["homePoints"] is not None and g["awayPoints"] is not None
        home_ls = g.get("homeLineScores") or []
        game_lines = [
            {
                "provider": _provider_slug(line["provider"]),
                "home_spread": line.get("spread"),
                "total": line.get("overUnder"),
                "home_moneyline": line.get("homeMoneyline"),
                "away_moneyline": line.get("awayMoneyline"),
            }
            for line in lines_by_game.get(g["id"], [])
            if line.get("spread") is not None or line.get("overUnder") is not None
            or (line.get("homeMoneyline") is not None and line.get("awayMoneyline") is not None)
        ]
        records.append({
            "external_id": str(g["id"]),
            "season_type": g["seasonType"] if g["seasonType"] in SEASON_TYPES else "postseason",
            "week": g["week"],
            "starts_at": datetime.fromisoformat(g["startDate"].replace("Z", "+00:00")),
            "start_time_tbd": bool(g["startTimeTBD"]),
            "home": _team(g, "home"),
            "away": _team(g, "away"),
            "neutral_site": bool(g["neutralSite"]),
            "venue": g.get("venue"),
            "status": "final" if final else "scheduled",
            "home_score": g["homePoints"] if final else None,
            "away_score": g["awayPoints"] if final else None,
            "overtime": (len(home_ls) > 4) if final and home_ls else None,
            "lines": game_lines,
        })
    return records


def _team(game: dict, side: str) -> dict:
    """Enough team info to create an opponent (e.g. an FCS school) that the
    FBS seed did not include."""
    name = game[f"{side}Team"]
    return {
        "external_id": str(game[f"{side}Id"]),
        "slug": None, "name": name, "short_name": name, "abbreviation": None,
        "mascot": None, "color": None, "alt_color": None, "logo_url": None,
        "conference": game.get(f"{side}Conference"), "division": None,
        "classification": game.get(f"{side}Classification"),
        "names": [name],
    }
