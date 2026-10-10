"""College football data from CollegeFootballData.com (free API key)."""

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
