# DelphiEdge v2

A football prediction model graded publicly against pundits and the betting
market. See [spec.md](spec.md) for scope and [CLAUDE.md](CLAUDE.md) for rules
and conventions.

## Setup

Requirements: Node 18.18+ (20+ recommended), Python 3.12+.

```sh
cp .env.example .env          # then fill in values
python3 -m venv .venv
.venv/bin/pip install -e "./jobs[dev]"
(cd web && npm install)
```

## Database

```sh
scripts/job delphi_jobs.migrate          # apply migrations
scripts/job delphi_jobs.seed_reference   # leagues, seasons, teams, outlets, experts
scripts/job delphi_jobs.ingest_games     # games, scores, closing lines (re-runnable)
scripts/job delphi_jobs.grade            # market benchmark + grades (re-runnable)
./scripts/test.sh                                # tests on a local throwaway Postgres
```

## Entering pundit picks

1. Copy `data/picks/template.csv` to `data/picks/2026/<league>-week<NN>.csv`
   (e.g. `nfl-week06.csv`). One row per writer per game.
2. Check it: `scripts/job delphi_jobs.import_picks data/picks/2026/nfl-week06.csv --dry-run`
3. Import it: the same command without `--dry-run`.

If any row has a problem, nothing is imported and each problem is listed by
row number. Re-importing a file is safe. Picks lock at kickoff.

| Column | Required | Meaning |
|---|---|---|
| league | yes | `nfl` or `cfb` |
| outlet | yes | `covers`, `sports-illustrated`, `cbs-sports`, `yahoo-sports` (or the outlet's name) |
| expert | yes | Writer's name. A new name creates the writer. |
| published_at | yes | When the pick was published, `YYYY-MM-DD HH:MM` in US Eastern time |
| away_team, home_team | yes | As listed on the schedule. Nicknames, cities and abbreviations work. |
| pick | no | Straight-up winner. Filled from the predicted score if blank. |
| ats_pick | no | Team picked against the spread |
| ats_line | no | The line the writer quoted for the ats_pick team, e.g. `+3.5`. Blank means grade against the closing line. |
| away_score, home_score | no | Predicted final score, both or neither |
| source_url, notes | no | Where the pick came from; anything else worth keeping |

## Automation

`.github/workflows/update-data.yml` runs ingestion and grading every 3 hours
on game days (Thu, Sat, Sun, Mon Eastern) and once a day otherwise. Run it by
hand from the Actions tab with "Run workflow". It needs two repository
secrets, `DATABASE_URL` and `CFBD_API_KEY`. It does not apply migrations: it
stops with an error if any are pending, so apply them with
`scripts/job delphi_jobs.migrate` after merging a new one.
