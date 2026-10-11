-- DelphiEdge v2: core schema (Milestone 1)
--
-- Conventions
--   * Sport-agnostic: everything hangs off sports -> leagues -> seasons.
--   * Spreads are stored from the HOME team's perspective using sportsbook
--     convention: negative = home favored (home -3.5 means home gives 3.5).
--   * Margins are home score minus away score.
--   * Sportsbook lines live in the separate `benchmark` schema (see 0002).
--     They are a grading benchmark only and must never be a model input.

create or replace function public.set_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

-- ---------------------------------------------------------------- sports ---
create table public.sports (
  id          smallint generated always as identity primary key,
  slug        text not null unique check (slug ~ '^[a-z0-9-]+$'),
  name        text not null,
  created_at  timestamptz not null default now()
);

create table public.leagues (
  id          smallint generated always as identity primary key,
  sport_id    smallint not null references public.sports(id),
  slug        text not null unique check (slug ~ '^[a-z0-9-]+$'),
  name        text not null,
  created_at  timestamptz not null default now()
);

create table public.seasons (
  id          integer generated always as identity primary key,
  league_id   smallint not null references public.leagues(id),
  year        smallint not null,          -- calendar year the season starts
  label       text not null,              -- e.g. '2026' or '2026-27'
  starts_on   date,
  ends_on     date,
  created_at  timestamptz not null default now(),
  unique (league_id, year)
);

-- ----------------------------------------------------------------- teams ---
create table public.teams (
  id            integer generated always as identity primary key,
  league_id     smallint not null references public.leagues(id),
  slug          text not null check (slug ~ '^[a-z0-9-]+$'),
  name          text not null,     -- 'Kansas City Chiefs', 'Texas A&M'
  short_name    text,              -- 'Chiefs', 'Texas A&M'
  abbreviation  text,
  mascot        text,
  color         text,
  alt_color     text,
  logo_url      text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  unique (league_id, slug)
);
create trigger teams_updated_at before update on public.teams
  for each row execute function public.set_updated_at();

-- Conference, division and level change between seasons (realignment,
-- FCS -> FBS moves), so they live per season rather than on the team.
create table public.team_seasons (
  team_id         integer not null references public.teams(id) on delete cascade,
  season_id       integer not null references public.seasons(id) on delete cascade,
  conference      text,
  division        text,
  classification  text,            -- 'fbs', 'fcs', ... ; null for pro leagues
  primary key (team_id, season_id)
);

-- Matching key for team names and IDs: lowercase letters and digits only,
-- so "Hawai'i", "Hawaii" and "HAWAII" match, as do "Miami (OH)" and
-- "Miami OH". jobs/delphi_jobs/seed_reference.py mirrors this in Python.
create or replace function public.alias_key(text) returns text
language sql immutable parallel safe as $$
  select regexp_replace(lower($1), '[^a-z0-9]+', '', 'g')
$$;

-- External IDs and alternate names, used to match source data and pundit
-- CSVs to teams. source = 'name' for human-readable aliases; otherwise the
-- data source the ID comes from ('cfbd', 'nflverse', ...).
create table public.team_aliases (
  id          integer generated always as identity primary key,
  team_id     integer not null references public.teams(id) on delete cascade,
  league_id   smallint not null references public.leagues(id),
  source      text not null check (source ~ '^[a-z0-9_]+$'),
  alias       text not null,
  alias_key   text generated always as (public.alias_key(alias)) stored
              check (alias_key <> ''),
  created_at  timestamptz not null default now(),
  unique (league_id, source, alias_key)
);
create index team_aliases_team_idx on public.team_aliases(team_id);

-- ----------------------------------------------------------------- games ---
create table public.games (
  id              bigint generated always as identity primary key,
  season_id       integer not null references public.seasons(id),
  season_type     text not null check (season_type in ('regular', 'postseason')),
  week            smallint,
  starts_at       timestamptz,                 -- null when not yet scheduled
  start_time_tbd  boolean not null default false,
  home_team_id    integer not null references public.teams(id),
  away_team_id    integer not null references public.teams(id),
  neutral_site    boolean not null default false,
  venue           text,
  status          text not null default 'scheduled'
                  check (status in ('scheduled', 'in_progress', 'final',
                                    'postponed', 'canceled')),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  check (home_team_id <> away_team_id)
);
create index games_season_week_idx on public.games(season_id, season_type, week);
create index games_starts_at_idx on public.games(starts_at);
create trigger games_updated_at before update on public.games
  for each row execute function public.set_updated_at();

create table public.game_external_ids (
  game_id      bigint not null references public.games(id) on delete cascade,
  source       text not null check (source ~ '^[a-z0-9_]+$'),
  external_id  text not null,
  primary key (source, external_id),
  unique (game_id, source)
);

create table public.results (
  game_id      bigint primary key references public.games(id) on delete cascade,
  home_score   smallint not null check (home_score >= 0),
  away_score   smallint not null check (away_score >= 0),
  overtime     boolean,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
create trigger results_updated_at before update on public.results
  for each row execute function public.set_updated_at();

-- --------------------------------------------------------------- experts ---
-- Publications that employ human experts (Covers, CBS Sports, ...).
create table public.outlets (
  id          integer generated always as identity primary key,
  slug        text not null unique check (slug ~ '^[a-z0-9-]+$'),
  name        text not null,
  url         text,
  created_at  timestamptz not null default now()
);

-- Human experts are tracked per writer, each tied to an outlet. The model
-- and market experts have no outlet.
create table public.experts (
  id          integer generated always as identity primary key,
  slug        text not null unique check (slug ~ '^[a-z0-9-]+$'),
  name        text not null,
  kind        text not null check (kind in ('human', 'model', 'market')),
  outlet_id   integer references public.outlets(id),
  url         text,
  active      boolean not null default true,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  check ((kind = 'human') = (outlet_id is not null))
);
create index experts_outlet_idx on public.experts(outlet_id);
create trigger experts_updated_at before update on public.experts
  for each row execute function public.set_updated_at();

-- ----------------------------------------------------------- predictions ---
-- One current prediction per expert per game. Every field is optional
-- because experts publish different things; grading uses what exists.
create table public.predictions (
  id                    bigint generated always as identity primary key,
  expert_id             integer not null references public.experts(id),
  game_id               bigint not null references public.games(id),
  published_at          timestamptz not null,
  -- straight up
  pick_team_id          integer references public.teams(id),
  home_win_prob         numeric(5,4) check (home_win_prob between 0 and 1),
  -- scores, margin (home - away) and total
  predicted_home_score  numeric(5,1) check (predicted_home_score >= 0),
  predicted_away_score  numeric(5,1) check (predicted_away_score >= 0),
  predicted_margin      numeric(5,1),
  predicted_total       numeric(5,1) check (predicted_total >= 0),
  -- against the spread: the pick plus the line the expert quoted, if any.
  -- When no line is quoted, grading falls back to the closing line.
  ats_pick_team_id      integer references public.teams(id),
  quoted_home_spread    numeric(4,1),
  -- provenance
  source                text not null,   -- 'csv', 'scraper:covers', 'model', ...
  source_url            text,
  notes                 text,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  unique (expert_id, game_id),
  check ((predicted_home_score is null) = (predicted_away_score is null)),
  check (quoted_home_spread is null or ats_pick_team_id is not null),
  check (pick_team_id is not null or home_win_prob is not null
         or predicted_margin is not null or ats_pick_team_id is not null
         or predicted_total is not null)
);
create index predictions_game_idx on public.predictions(game_id);

-- Integrity rules for a public scorecard:
--   1. Picked teams must be one of the two teams in the game.
--   2. Margin and total are derived from predicted scores and must agree.
--   3. A prediction must be published before kickoff.
--   4. After kickoff a prediction is locked. A deliberate correction can be
--      made inside a transaction that runs
--        set local delphi.allow_locked_edit = 'on';
create or replace function public.predictions_enforce_integrity()
returns trigger language plpgsql as $$
declare
  g public.games%rowtype;
begin
  select * into g from public.games where id = new.game_id;

  if new.pick_team_id is not null
     and new.pick_team_id not in (g.home_team_id, g.away_team_id) then
    raise exception 'pick_team_id % is not in game %', new.pick_team_id, g.id;
  end if;
  if new.ats_pick_team_id is not null
     and new.ats_pick_team_id not in (g.home_team_id, g.away_team_id) then
    raise exception 'ats_pick_team_id % is not in game %', new.ats_pick_team_id, g.id;
  end if;

  if new.predicted_home_score is not null then
    if new.predicted_margin is null then
      new.predicted_margin := new.predicted_home_score - new.predicted_away_score;
    end if;
    if new.predicted_total is null then
      new.predicted_total := new.predicted_home_score + new.predicted_away_score;
    end if;
    if new.predicted_margin <> new.predicted_home_score - new.predicted_away_score
       or new.predicted_total <> new.predicted_home_score + new.predicted_away_score then
      raise exception 'predicted margin/total do not match predicted scores';
    end if;
  end if;

  if g.starts_at is not null and new.published_at >= g.starts_at then
    raise exception 'prediction published at % is not before kickoff %',
      new.published_at, g.starts_at;
  end if;

  if tg_op = 'UPDATE'
     and g.starts_at is not null and now() >= g.starts_at
     and coalesce(current_setting('delphi.allow_locked_edit', true), '') <> 'on'
     and (new.pick_team_id, new.home_win_prob, new.predicted_home_score,
          new.predicted_away_score, new.predicted_margin, new.predicted_total,
          new.ats_pick_team_id, new.quoted_home_spread, new.published_at,
          new.expert_id, new.game_id)
         is distinct from
         (old.pick_team_id, old.home_win_prob, old.predicted_home_score,
          old.predicted_away_score, old.predicted_margin, old.predicted_total,
          old.ats_pick_team_id, old.quoted_home_spread, old.published_at,
          old.expert_id, old.game_id) then
    raise exception 'prediction % is locked: game % has kicked off', old.id, g.id;
  end if;

  return new;
end;
$$;

create trigger predictions_integrity
  before insert or update on public.predictions
  for each row execute function public.predictions_enforce_integrity();
create trigger predictions_updated_at before update on public.predictions
  for each row execute function public.set_updated_at();

-- Deleting a graded pick would quietly improve a record, so deletes are
-- locked after kickoff under the same rule.
create or replace function public.predictions_block_locked_delete()
returns trigger language plpgsql as $$
declare
  kickoff timestamptz;
begin
  select starts_at into kickoff from public.games where id = old.game_id;
  if kickoff is not null and now() >= kickoff
     and coalesce(current_setting('delphi.allow_locked_edit', true), '') <> 'on' then
    raise exception 'prediction % is locked: game % has kicked off', old.id, old.game_id;
  end if;
  return old;
end;
$$;

create trigger predictions_locked_delete
  before delete on public.predictions
  for each row execute function public.predictions_block_locked_delete();
