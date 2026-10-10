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
.venv/bin/python -m delphi_jobs.migrate          # apply migrations
.venv/bin/python -m delphi_jobs.seed_reference   # leagues, seasons, teams, experts
./scripts/test.sh                                # tests on a local throwaway Postgres
```
