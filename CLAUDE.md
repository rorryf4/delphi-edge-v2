# DelphiEdge v2

Read spec.md first. No features outside the spec without updating the spec.

## Non-negotiable rules
- The DelphiEdge model is BLIND to sportsbook lines. Lines live only in the
  `benchmark` Postgres schema. Model code must never read `benchmark.*` or
  `grades` (which stores the line each ATS pick was graded against), and
  must never read predictions from the `market` expert.
- Predictions are locked at kickoff by database triggers. Do not weaken them.
  A deliberate correction uses `set local delphi.allow_locked_edit = 'on'`.

- Head-to-head comparisons between experts use only shared games, per metric.
  Use the `head_to_head()` SQL function; never compare two experts' overall
  leaderboard rows directly. Every displayed number shows its game count.

## Conventions
- Spreads are from the home team's view, sportsbook style: negative = home favored.
  (nflverse's `spread_line` is the opposite sign; convert on ingestion.)
- Margin = home score minus away score.
- Teams are matched by source ID via `team_aliases`, never by name alone.

## Layout
- `db/migrations/`  SQL migrations, applied in filename order
- `jobs/`           Python jobs (package `delphi_jobs`) and tests
- `web/`            Next.js + TypeScript site
- `scripts/test.sh` runs Python tests against a throwaway local Postgres

## Commands
- Tests: `./scripts/test.sh`
- Migrate: `scripts/job delphi_jobs.migrate` (add `--status` to check)
- Seed reference data: `scripts/job delphi_jobs.seed_reference`
- Import pundit picks: `scripts/job delphi_jobs.import_picks <file.csv> [--dry-run]` (template: data/picks/template.csv)
- Grade (derives market benchmark, grades all finals, idempotent): `scripts/job delphi_jobs.grade`
- Ingest games, scores, closing lines (whole season, idempotent): `scripts/job delphi_jobs.ingest_games`
- Run Python via `scripts/job`, not the venv directly: macOS hides the editable
  install's .pth file here and Python 3.13 then skips it.
- Web: `cd web && npm run dev` / `npm run build` / `npm run lint`
