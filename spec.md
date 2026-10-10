# DelphiEdge v2 — Spec

## Mission
Be the most accurate public football predictor, and prove it on a transparent scorecard.
Betting comes downstream of accuracy, not the other way around.

## Core principles
- The DelphiEdge model is BLIND to sportsbook lines. It never uses spreads, totals,
  or odds as inputs.
- The model is forward-looking: it weighs what's true about the teams *today*
  (who's playing, how units match up) over what happened in past games.
- Every prediction is graded publicly, wins and misses alike.

## v1 scope
- Leagues: NFL and college football (FBS), 2026 season
- Later: college basketball, NBA player props (keep the data model sport-agnostic)

## Model output (every game)
- Win probability for each team
- Predicted margin
- Predicted total points

## Model inputs (v1 priorities)
1. Roster & availability: injuries, practice reports, QB/starter changes, depth chart
   moves, pulled from beat reporters, team reports, and press conferences, with
   Claude extracting structured signals from the text
2. Unit-vs-unit matchups: built from play-by-play data (e.g., offensive line vs.
   pass rush, secondary vs. deep passing)
3. A baseline team strength rating to anchor the above

## Scorecard
Everyone is graded on the same leaderboard:
- DelphiEdge model
- Pundits: Covers, Sports Illustrated, CBS Sports, Yahoo Sports (add more later)
- The market (closing line), as a BENCHMARK ONLY, never a model input

Grading:
- Pick accuracy: straight-up and against the spread (all experts)
- Margin error: average points off (anyone who publishes predicted scores)
- Brier score: win-probability accuracy (DelphiEdge + market)

## Pages
1. Leaderboard: all experts ranked, filterable by league and metric
2. Expert page: full prediction history with results
3. Week view: every expert's prediction per game side by side, with the result

## Data model (sport-agnostic)
sports, leagues, seasons, games, teams, players, availability_reports,
experts (human / model / market), predictions, results

## Tech
- Next.js + TypeScript frontend on Vercel
- Postgres (Supabase)
- Python jobs on a schedule (GitHub Actions) for scraping, news extraction, and grading
- No features outside this spec without updating the spec first

## Milestones
1. Database, game/score ingestion, manual CSV entry for pundit picks, grading logic,
   basic leaderboard
2. Pundit scrapers (start with 2 sources), week view, expert pages
3. DelphiEdge baseline model + matchup layer
4. News/availability extraction layer
5. Later: college basketball, NBA props

## Definition of done for v1
Live public URL. Updates weekly without manual work. Shows DelphiEdge, at least
3 pundits, and the market benchmark across NFL and CFB, graded on all metrics.