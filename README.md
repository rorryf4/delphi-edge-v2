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
./scripts/test.sh                                # tests on a local throwaway Postgres
```
