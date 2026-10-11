"""Import pundit picks from CSV files (template: data/picks/template.csv).

The whole file is checked first. If any row has a problem, nothing is
imported and every problem is listed with its row number. Re-importing the
same file changes nothing; an edited row updates that pick, as long as the
game has not kicked off (picks lock at kickoff).

Usage:  scripts/job delphi_jobs.import_picks data/picks/2026/nfl-week06.csv [--dry-run]
"""

import argparse
import csv
import difflib
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg

from .db import connect
from .seed_reference import alias_key, slugify

EASTERN = ZoneInfo("America/New_York")

COLUMNS = ["league", "outlet", "expert", "published_at", "away_team", "home_team",
           "pick", "ats_pick", "ats_line", "away_score", "home_score", "source_url", "notes"]
REQUIRED = {"league", "outlet", "expert", "published_at", "away_team", "home_team"}

# A pick must be for a game starting within this long after publication.
MATCH_WINDOW = timedelta(days=14)

PREDICTION_FIELDS = ("published_at", "pick_team_id", "predicted_home_score",
                     "predicted_away_score", "ats_pick_team_id", "quoted_home_spread",
                     "source_url", "notes")


class RowError(Exception):
    pass


@dataclass
class Pick:
    line: int
    expert_slug: str
    expert_name: str
    outlet_id: int
    game_id: int
    values: dict


def parse_time(text: str) -> datetime:
    """'2026-10-14 10:00' (US Eastern) or ISO 8601 with an offset."""
    try:
        value = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        raise RowError(f"published_at {text!r} is not a date and time like 2026-10-14 10:00")
    if len(text.strip()) <= 10:
        raise RowError(f"published_at {text!r} needs a time as well, like 2026-10-14 10:00")
    if value.tzinfo is None:
        value = value.replace(tzinfo=EASTERN)
    return value.astimezone(timezone.utc)


def parse_number(text: str, column: str) -> float | None:
    text = text.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        raise RowError(f"{column} {text!r} is not a number")


def read_rows(path: Path) -> list[tuple[int, dict]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = [h.strip() for h in (reader.fieldnames or [])]
        missing = REQUIRED - set(header)
        unknown = set(header) - set(COLUMNS)
        if missing or unknown:
            problems = []
            if missing:
                problems.append("missing columns: " + ", ".join(sorted(missing)))
            if unknown:
                problems.append("unknown columns: " + ", ".join(sorted(unknown)))
            raise RowError(f"{path}: " + "; ".join(problems) + ". Use data/picks/template.csv.")
        rows = []
        for line, raw in enumerate(reader, start=2):
            row = {k.strip(): (v or "").strip() for k, v in raw.items() if k}
            if any(row.values()):
                rows.append((line, row))
        return rows


class Resolver:
    def __init__(self, conn: psycopg.Connection, now: datetime):
        self.conn = conn
        self.now = now
        self.leagues = dict(conn.execute("select slug, id from leagues").fetchall())
        self.outlets = {}
        for oid, slug, name in conn.execute("select id, slug, name from outlets").fetchall():
            self.outlets[alias_key(slug)] = (oid, name)
            self.outlets[alias_key(name)] = (oid, name)
        self.aliases: dict[int, dict[str, set[int]]] = {}
        self.alias_names: dict[int, dict[str, str]] = {}
        for league_id, key, alias, team_id in conn.execute(
                "select league_id, alias_key, alias, team_id from team_aliases"):
            self.aliases.setdefault(league_id, {}).setdefault(key, set()).add(team_id)
            self.alias_names.setdefault(league_id, {})[key] = alias
        self.team_names = dict(conn.execute("select id, name from teams").fetchall())
        self.experts = {slug: (eid, outlet_id) for slug, eid, outlet_id in conn.execute(
            "select slug, id, outlet_id from experts")}

    def team(self, league_id: int, text: str, column: str) -> int:
        ids = self.aliases.get(league_id, {}).get(alias_key(text), set())
        if len(ids) == 1:
            return next(iter(ids))
        names = self.alias_names.get(league_id, {})
        close = difflib.get_close_matches(alias_key(text), list(names), n=3, cutoff=0.6)
        hint = f" Did you mean: {', '.join(names[k] for k in close)}?" if close else ""
        raise RowError(f"{column} {text!r} does not match a team.{hint}")

    def game(self, league_id: int, away_id: int, home_id: int, published_at: datetime) -> dict:
        rows = self.conn.execute(
            """select g.id, g.home_team_id, g.away_team_id, g.starts_at, g.start_time_tbd
               from games g join seasons s on s.id = g.season_id
               where s.league_id = %s and g.starts_at > %s and g.starts_at <= %s
                 and %s in (g.home_team_id, g.away_team_id)
                 and %s in (g.home_team_id, g.away_team_id)
               order by g.starts_at limit 1""",
            (league_id, published_at - timedelta(days=1), published_at + MATCH_WINDOW,
             away_id, home_id),
        ).fetchone()
        if rows is None:
            raise RowError(
                f"no {self.team_names[away_id]} vs {self.team_names[home_id]} game within "
                f"{MATCH_WINDOW.days} days after published_at")
        gid, g_home, g_away, starts_at, tbd = rows
        if (g_home, g_away) != (home_id, away_id):
            raise RowError(
                f"home and away are swapped: the schedule has {self.team_names[g_away]} at "
                f"{self.team_names[g_home]}")
        if not tbd and published_at >= starts_at:
            raise RowError(
                f"published_at is not before kickoff "
                f"({starts_at.astimezone(EASTERN):%Y-%m-%d %H:%M} ET)")
        return {"id": gid, "home": g_home, "away": g_away}

    def row(self, line: int, r: dict, source: str) -> Pick:
        league_id = self.leagues.get(r.get("league", "").lower())
        if league_id is None:
            raise RowError(f"league {r.get('league')!r} must be one of: {', '.join(sorted(self.leagues))}")
        outlet = self.outlets.get(alias_key(r.get("outlet", "")))
        if outlet is None:
            raise RowError(f"outlet {r.get('outlet')!r} is not a known outlet")
        expert_name = r.get("expert", "")
        expert_slug = slugify(expert_name)
        if not expert_slug:
            raise RowError("expert is blank")
        if expert_slug in ("delphiedge", "market"):
            raise RowError(f"expert {expert_name!r} is reserved")
        known = self.experts.get(expert_slug)
        if known and known[1] != outlet[0]:
            raise RowError(f"expert {expert_name!r} already exists under a different outlet")

        published_at = parse_time(r.get("published_at", ""))
        if published_at > self.now + timedelta(minutes=5):
            raise RowError("published_at is in the future")

        away_id = self.team(league_id, r.get("away_team", ""), "away_team")
        home_id = self.team(league_id, r.get("home_team", ""), "home_team")
        if away_id == home_id:
            raise RowError("away_team and home_team are the same team")
        game = self.game(league_id, away_id, home_id, published_at)

        def side(column: str) -> int | None:
            text = r.get(column, "")
            if not text:
                return None
            tid = self.team(league_id, text, column)
            if tid not in (home_id, away_id):
                raise RowError(f"{column} {text!r} is not playing in this game")
            return tid

        pick_id = side("pick")
        ats_id = side("ats_pick")
        ats_line = parse_number(r.get("ats_line", ""), "ats_line")
        away_score = parse_number(r.get("away_score", ""), "away_score")
        home_score = parse_number(r.get("home_score", ""), "home_score")

        if ats_line is not None and ats_id is None:
            raise RowError("ats_line needs an ats_pick")
        if ats_line is not None and (ats_line * 2) != int(ats_line * 2):
            raise RowError(f"ats_line {ats_line} must be a whole or half point")
        if (away_score is None) != (home_score is None):
            raise RowError("give both away_score and home_score, or neither")
        if home_score is not None:
            if home_score < 0 or away_score < 0:
                raise RowError("scores cannot be negative")
            score_winner = (home_id if home_score > away_score
                            else away_id if away_score > home_score else None)
            if pick_id is None:
                pick_id = score_winner
            elif score_winner is not None and pick_id != score_winner:
                raise RowError("pick disagrees with the predicted score")
        if pick_id is None and ats_id is None and home_score is None:
            raise RowError("row has no pick, ats_pick or predicted score")

        # ats_line is the line for the team picked against the spread, as
        # pundits write it ("Seahawks +3.5"). Store it from the home side.
        quoted_home_spread = None
        if ats_line is not None:
            quoted_home_spread = ats_line if ats_id == home_id else -ats_line

        return Pick(
            line=line, expert_slug=expert_slug, expert_name=expert_name,
            outlet_id=outlet[0], game_id=game["id"],
            values={
                "published_at": published_at,
                "pick_team_id": pick_id,
                "predicted_home_score": home_score,
                "predicted_away_score": away_score,
                "ats_pick_team_id": ats_id,
                "quoted_home_spread": quoted_home_spread,
                "source_url": r.get("source_url") or None,
                "notes": r.get("notes") or None,
                "source": source,
            },
        )


def resolve(conn: psycopg.Connection, path: Path, now: datetime) -> tuple[list[Pick], list[str]]:
    try:
        rows = read_rows(path)
    except RowError as e:
        return [], [str(e)]
    resolver = Resolver(conn, now)
    picks, errors, seen = [], [], {}
    source = f"csv:{path.name}"
    for line, r in rows:
        try:
            pick = resolver.row(line, r, source)
        except RowError as e:
            errors.append(f"row {line}: {e}")
            continue
        key = (pick.expert_slug, pick.game_id)
        if key in seen:
            errors.append(f"row {line}: same expert and game as row {seen[key]}")
            continue
        seen[key] = line
        picks.append(pick)
    if not rows:
        errors.append(f"{path}: no pick rows")
    return picks, errors


def write(conn: psycopg.Connection, picks: list[Pick]) -> dict[str, int]:
    counts = {"inserted": 0, "updated": 0, "unchanged": 0, "experts_created": 0}
    for p in picks:
        row = conn.execute("select id from experts where slug = %s", (p.expert_slug,)).fetchone()
        if row:
            expert_id = row[0]
        else:
            expert_id = conn.execute(
                """insert into experts (slug, name, kind, outlet_id)
                   values (%s, %s, 'human', %s) returning id""",
                (p.expert_slug, p.expert_name, p.outlet_id),
            ).fetchone()[0]
            counts["experts_created"] += 1
        existing = conn.execute(
            f"select id, {', '.join(PREDICTION_FIELDS)} from predictions "
            "where expert_id = %s and game_id = %s",
            (expert_id, p.game_id),
        ).fetchone()
        values = {**p.values, "expert_id": expert_id, "game_id": p.game_id}
        if existing is None:
            cols = ", ".join(values)
            params = ", ".join(f"%({c})s" for c in values)
            _execute(conn, p.line, f"insert into predictions ({cols}) values ({params})", values)
            counts["inserted"] += 1
            continue
        current = dict(zip(PREDICTION_FIELDS, existing[1:]))
        wanted = {f: p.values[f] for f in PREDICTION_FIELDS}
        if all(_same(current[f], wanted[f]) for f in PREDICTION_FIELDS):
            counts["unchanged"] += 1
            continue
        sets = ", ".join(f"{f} = %({f})s" for f in (*PREDICTION_FIELDS, "source"))
        _execute(conn, p.line, f"update predictions set {sets} where id = %(id)s",
                 {**values, "id": existing[0]})
        counts["updated"] += 1
    return counts


def _execute(conn: psycopg.Connection, line: int, sql: str, params: dict) -> None:
    """Run a write, turning a database rule violation into a row error."""
    try:
        with conn.transaction():
            conn.execute(sql, params)
    except (psycopg.errors.RaiseException, psycopg.errors.IntegrityError) as e:
        raise RowError(f"row {line}: {e.diag.message_primary}") from None


def _same(a, b) -> bool:
    if a is None or b is None:
        return a is b
    if isinstance(a, datetime) or isinstance(b, datetime):
        return a == b
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return a == b


def import_file(conn: psycopg.Connection, path: Path, dry_run: bool = False,
                now: datetime | None = None) -> tuple[dict[str, int] | None, list[str]]:
    now = now or datetime.now(timezone.utc)
    with conn.transaction(force_rollback=dry_run):
        picks, errors = resolve(conn, path, now)
        if errors:
            return None, errors
        try:
            with conn.transaction():
                counts = write(conn, picks)
        except RowError as e:
            return None, [str(e)]
    return counts, []


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--dry-run", action="store_true",
                        help="check the file and report what would change, without saving")
    args = parser.parse_args(argv)
    failed = False
    with connect() as conn:
        for path in args.files:
            if not path.is_file():
                print(f"{path}: file not found")
                failed = True
                continue
            counts, errors = import_file(conn, path, dry_run=args.dry_run)
            if errors:
                failed = True
                print(f"{path}: NOT imported. Fix these and re-run:")
                for e in errors:
                    print(f"  {e}")
            else:
                verb = "would import" if args.dry_run else "imported"
                summary = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in counts.items())
                print(f"{path}: {verb}: {summary}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
