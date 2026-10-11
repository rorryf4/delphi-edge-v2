"""Grade predictions on final games, and derive the market benchmark.

1. Market benchmark. For every game with a closing line, the `market`
   expert gets a prediction derived from it:
     win probability  from the closing moneylines with the vig removed
     margin           minus the closing home spread
     straight-up pick the moneyline favorite (or the spread favorite)
   The market makes no against-the-spread pick: graded against its own
   closing line it would always be a coin flip.
2. Grades. Every prediction on a final game is graded (see
   db/migrations/0003_grading.sql for definitions). Grades are recomputed
   on every run, so score and line corrections flow through.

Grading reads benchmark.* by design. Model code must not.

Usage:  scripts/job delphi_jobs.grade
"""

import sys
from collections import defaultdict
from datetime import timedelta

import psycopg

from .db import connect

# Closing-line provider preference per league; others follow alphabetically.
PROVIDER_PREFERENCE = {
    "nfl": ["nflverse"],
    "cfb": ["cfbd:draftkings", "cfbd:bovada"],
}


# ----------------------------------------------------------------- math ---
def implied_probability(moneyline: float) -> float:
    """American odds to implied probability (vig included)."""
    if moneyline < 0:
        return -moneyline / (-moneyline + 100.0)
    return 100.0 / (moneyline + 100.0)


def no_vig_home_probability(home_ml: float | None, away_ml: float | None) -> float | None:
    """Home win probability with the vig removed by normalizing the two
    implied probabilities so they sum to 1."""
    if home_ml is None or away_ml is None:
        return None
    home, away = implied_probability(float(home_ml)), implied_probability(float(away_ml))
    return home / (home + away)


def grade_prediction(p: dict, home_score: int, away_score: int,
                     closing_spread: float | None) -> dict:
    """Grade one prediction. p has home_team_id, away_team_id, pick_team_id,
    ats_pick_team_id, quoted_home_spread, predicted_margin, home_win_prob."""
    reasons = []
    margin = home_score - away_score
    winner = (p["home_team_id"] if margin > 0 else p["away_team_id"] if margin < 0 else None)

    su = None
    if p["pick_team_id"] is not None:
        su = "push" if winner is None else ("win" if p["pick_team_id"] == winner else "loss")

    ats = ats_line = ats_source = None
    if p["ats_pick_team_id"] is not None:
        if p["quoted_home_spread"] is not None:
            ats_line, ats_source = float(p["quoted_home_spread"]), "quoted"
        elif closing_spread is not None:
            ats_line, ats_source = float(closing_spread), "closing"
        if ats_line is None:
            reasons.append("no quoted or closing line to grade the ATS pick")
        else:
            home_cover = margin + ats_line
            if home_cover == 0:
                ats = "push"
            else:
                covered = p["home_team_id"] if home_cover > 0 else p["away_team_id"]
                ats = "win" if p["ats_pick_team_id"] == covered else "loss"

    margin_error = None
    if p["predicted_margin"] is not None:
        margin_error = abs(float(p["predicted_margin"]) - margin)

    brier = None
    if p["home_win_prob"] is not None:
        if winner is None:
            reasons.append("game tied: win probability not scored")
        else:
            outcome = 1.0 if winner == p["home_team_id"] else 0.0
            brier = round((float(p["home_win_prob"]) - outcome) ** 2, 5)

    return {
        "su_result": su, "ats_result": ats, "ats_line": ats_line,
        "ats_line_source": ats_source if ats_line is not None else None,
        "margin_error": margin_error, "brier": brier,
        "ungraded_reason": "; ".join(reasons) or None,
    }


# --------------------------------------------------------- closing lines ---
def closing_lines(conn: psycopg.Connection) -> dict[int, dict]:
    """Per game: the preferred closing spread, total and moneylines.
    Each value comes from the most preferred provider that has it."""
    by_game: dict[int, list] = defaultdict(list)
    league_of: dict[int, str] = {}
    for gid, league, provider, spread, total, hml, aml in conn.execute(
        """select m.game_id, l.slug, m.provider, m.home_spread, m.total,
                  m.home_moneyline, m.away_moneyline
           from benchmark.market_lines m
           join games g on g.id = m.game_id
           join seasons s on s.id = g.season_id
           join leagues l on l.id = s.league_id
           where m.is_closing"""
    ):
        by_game[gid].append((provider, spread, total, hml, aml))
        league_of[gid] = league
    chosen = {}
    for gid, rows in by_game.items():
        pref = PROVIDER_PREFERENCE.get(league_of[gid], [])
        rows.sort(key=lambda r: (pref.index(r[0]) if r[0] in pref else len(pref), r[0]))
        spread = next(((r[0], r[1]) for r in rows if r[1] is not None), (None, None))
        total = next((r[2] for r in rows if r[2] is not None), None)
        ml = next(((r[0], r[3], r[4]) for r in rows
                   if r[3] is not None and r[4] is not None), (None, None, None))
        chosen[gid] = {"spread_provider": spread[0], "home_spread": spread[1], "total": total,
                       "ml_provider": ml[0], "home_moneyline": ml[1], "away_moneyline": ml[2]}
    return chosen


def market_prediction(line: dict, home_id: int, away_id: int) -> dict | None:
    prob = no_vig_home_probability(line["home_moneyline"], line["away_moneyline"])
    spread = line["home_spread"]
    if prob is not None and prob != 0.5:
        pick = home_id if prob > 0.5 else away_id
    elif spread is not None and spread != 0:
        pick = home_id if spread < 0 else away_id
    else:
        pick = None
    if prob is None and spread is None and pick is None:
        return None
    providers = sorted({p for p in (line["spread_provider"], line["ml_provider"]) if p})
    return {
        "pick_team_id": pick,
        "home_win_prob": None if prob is None else round(prob, 4),
        "predicted_margin": None if spread is None else -float(spread),
        "predicted_total": None if line["total"] is None else float(line["total"]),
        "source": "market:" + "+".join(providers),
    }


def derive_market(conn: psycopg.Connection, lines: dict[int, dict]) -> int:
    """Upsert the market expert's predictions. Returns rows changed."""
    expert_id = conn.execute("select id from experts where slug = 'market'").fetchone()[0]
    games = {gid: (starts_at, home, away) for gid, starts_at, home, away in conn.execute(
        "select id, starts_at, home_team_id, away_team_id from games where id = any(%s)",
        (list(lines),))}
    rows = []
    for gid, line in lines.items():
        starts_at, home, away = games[gid]
        pred = market_prediction(line, home, away)
        if pred is None:
            continue
        # The closing line is the market's view just before kickoff.
        rows.append({**pred, "expert_id": expert_id, "game_id": gid,
                     "published_at": starts_at - timedelta(seconds=1)})
    if not rows:
        return 0
    # Closing lines are stored after kickoff, so the market's predictions are
    # written after kickoff too. This is derived benchmark data, not anyone's
    # pick, so refreshing it uses the documented lock override. Scoped to
    # this transaction only.
    conn.execute("set local delphi.allow_locked_edit = 'on'")
    with conn.cursor() as cur:
        cur.executemany(
            """insert into predictions (expert_id, game_id, published_at, pick_team_id,
                 home_win_prob, predicted_margin, predicted_total, source)
               values (%(expert_id)s, %(game_id)s, %(published_at)s, %(pick_team_id)s,
                 %(home_win_prob)s, %(predicted_margin)s, %(predicted_total)s, %(source)s)
               on conflict (expert_id, game_id) do update set
                 published_at = excluded.published_at, pick_team_id = excluded.pick_team_id,
                 home_win_prob = excluded.home_win_prob,
                 predicted_margin = excluded.predicted_margin,
                 predicted_total = excluded.predicted_total, source = excluded.source
               where (predictions.published_at, predictions.pick_team_id,
                      predictions.home_win_prob, predictions.predicted_margin,
                      predictions.predicted_total, predictions.source)
                     is distinct from
                     (excluded.published_at, excluded.pick_team_id, excluded.home_win_prob,
                      excluded.predicted_margin, excluded.predicted_total, excluded.source)""",
            rows,
        )
        changed = cur.rowcount
    conn.execute("set local delphi.allow_locked_edit = 'off'")
    return changed


# ---------------------------------------------------------------- grades ---
def grade_all(conn: psycopg.Connection, lines: dict[int, dict]) -> dict[str, int]:
    preds = conn.execute(
        """select p.id, p.game_id, g.home_team_id, g.away_team_id, p.pick_team_id,
                  p.ats_pick_team_id, p.quoted_home_spread, p.predicted_margin,
                  p.home_win_prob, r.home_score, r.away_score
           from predictions p
           join games g on g.id = p.game_id and g.status = 'final'
           join results r on r.game_id = g.id"""
    ).fetchall()
    cols = ("id", "game_id", "home_team_id", "away_team_id", "pick_team_id", "ats_pick_team_id",
            "quoted_home_spread", "predicted_margin", "home_win_prob", "home_score", "away_score")
    rows = []
    for values in preds:
        p = dict(zip(cols, values))
        closing = lines.get(p["game_id"], {}).get("home_spread")
        g = grade_prediction(p, p["home_score"], p["away_score"], closing)
        rows.append({**g, "prediction_id": p["id"], "game_id": p["game_id"]})
    with conn.cursor() as cur:
        # Grades only exist for final games; clear any whose game is no longer final.
        cur.execute("""delete from grades gr using games g
                       where g.id = gr.game_id and g.status <> 'final'""")
        removed = cur.rowcount
        changed = 0
        if rows:
            cur.executemany(
                """insert into grades (prediction_id, game_id, su_result, ats_result, ats_line,
                     ats_line_source, margin_error, brier, ungraded_reason)
                   values (%(prediction_id)s, %(game_id)s, %(su_result)s, %(ats_result)s,
                     %(ats_line)s, %(ats_line_source)s, %(margin_error)s, %(brier)s,
                     %(ungraded_reason)s)
                   on conflict (prediction_id) do update set
                     su_result = excluded.su_result, ats_result = excluded.ats_result,
                     ats_line = excluded.ats_line, ats_line_source = excluded.ats_line_source,
                     margin_error = excluded.margin_error, brier = excluded.brier,
                     ungraded_reason = excluded.ungraded_reason, graded_at = now()
                   where (grades.su_result, grades.ats_result, grades.ats_line,
                          grades.ats_line_source, grades.margin_error, grades.brier,
                          grades.ungraded_reason)
                         is distinct from
                         (excluded.su_result, excluded.ats_result, excluded.ats_line,
                          excluded.ats_line_source, excluded.margin_error, excluded.brier,
                          excluded.ungraded_reason)""",
                rows,
            )
            changed = cur.rowcount
    return {"predictions_on_final_games": len(rows), "grades_changed": changed,
            "grades_removed": removed}


def run(conn: psycopg.Connection) -> dict[str, int]:
    """Derive the market benchmark and grade everything, all or nothing."""
    with conn.transaction():
        lines = closing_lines(conn)
        market_changed = derive_market(conn, lines)
        counts = grade_all(conn, lines)
    return {"market_predictions_changed": market_changed, **counts}


def main(argv: list[str]) -> int:
    with connect() as conn:
        counts = run(conn)
        print(", ".join(f"{k.replace('_', ' ')} {v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
