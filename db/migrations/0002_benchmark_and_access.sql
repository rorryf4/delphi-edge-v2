-- DelphiEdge v2: market benchmark isolation and public read access.

-- ------------------------------------------------------------ benchmark ---
-- Sportsbook lines are a BENCHMARK ONLY. They live in their own schema,
-- which Supabase does not expose through its API and which the public
-- roles cannot read. Only ingestion and grading jobs touch this schema.
-- Model code must never query it.
create schema benchmark;
revoke all on schema benchmark from public;

create table benchmark.market_lines (
  id              bigint generated always as identity primary key,
  game_id         bigint not null references public.games(id) on delete cascade,
  provider        text not null,          -- 'nflverse', 'cfbd:consensus', ...
  is_closing      boolean not null default false,
  home_spread     numeric(4,1),           -- negative = home favored
  total           numeric(5,1),
  home_moneyline  integer,                -- American odds
  away_moneyline  integer,
  captured_at     timestamptz not null default now(),
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  check (home_spread is not null or total is not null
         or (home_moneyline is not null and away_moneyline is not null))
);
create unique index market_lines_one_closing_per_provider
  on benchmark.market_lines(game_id, provider) where is_closing;
create index market_lines_game_idx on benchmark.market_lines(game_id);
create trigger market_lines_updated_at before update on benchmark.market_lines
  for each row execute function public.set_updated_at();

comment on schema benchmark is
  'Sportsbook lines. Grading benchmark only. Never a DelphiEdge model input.';

do $$
begin
  if exists (select 1 from pg_roles where rolname = 'anon') then
    execute 'revoke all on all tables in schema benchmark from anon, authenticated';
    execute 'revoke all on schema benchmark from anon, authenticated';
  end if;
end;
$$;

-- --------------------------------------------------- row level security ---
-- With RLS on and no policy, the publishable key (anon role) sees nothing.
-- Public scorecard tables get a read-only policy. Writes happen only through
-- the secret key or a direct database connection, both of which bypass RLS.
alter table public.sports            enable row level security;
alter table public.leagues           enable row level security;
alter table public.seasons           enable row level security;
alter table public.teams             enable row level security;
alter table public.team_seasons      enable row level security;
alter table public.team_aliases      enable row level security;
alter table public.games             enable row level security;
alter table public.game_external_ids enable row level security;
alter table public.results           enable row level security;
alter table public.experts           enable row level security;
alter table public.predictions       enable row level security;

create policy public_read on public.sports       for select using (true);
create policy public_read on public.leagues      for select using (true);
create policy public_read on public.seasons      for select using (true);
create policy public_read on public.teams        for select using (true);
create policy public_read on public.team_seasons for select using (true);
create policy public_read on public.games        for select using (true);
create policy public_read on public.results      for select using (true);
create policy public_read on public.experts      for select using (true);
create policy public_read on public.predictions  for select using (true);
-- team_aliases and game_external_ids are internal matching tables: no policy.
