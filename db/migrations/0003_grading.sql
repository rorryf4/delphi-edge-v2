-- DelphiEdge v2: grading (Milestone 1).
--
-- One grade per prediction on a final game, recomputed by the grading job
-- (jobs/delphi_jobs/grade.py) on every run.
--
-- Results are from the expert's point of view:
--   su_result   win / loss / push (push = the game ended tied)
--   ats_result  win / loss / push against ats_line
--   ats_line    the line used, from the home side (negative = home favored):
--               the expert's quoted line if given, else the closing line
--   margin_error |predicted margin - actual margin|
--   brier       (home_win_prob - home_won)^2; ties are not scored
-- A null result means the expert did not make that kind of pick, or the
-- data to grade it is missing (see ungraded_reason).
--
-- grades.ats_line can hold a closing line, so like benchmark.* it must never
-- be read by model code.

create table public.grades (
  prediction_id    bigint primary key references public.predictions(id) on delete cascade,
  game_id          bigint not null references public.games(id) on delete cascade,
  su_result        text check (su_result in ('win', 'loss', 'push')),
  ats_result       text check (ats_result in ('win', 'loss', 'push')),
  ats_line         numeric(4,1),
  ats_line_source  text check (ats_line_source in ('quoted', 'closing')),
  margin_error     numeric(5,1) check (margin_error >= 0),
  brier            numeric(6,5) check (brier between 0 and 1),
  ungraded_reason  text,
  graded_at        timestamptz not null default now(),
  check ((ats_result is null) or (ats_line is not null and ats_line_source is not null))
);
create index grades_game_idx on public.grades(game_id);

alter table public.grades enable row level security;
create policy public_read on public.grades for select using (true);

-- Leaderboard: one row per expert per league per season.
create view public.leaderboard with (security_invoker = true) as
select
  e.id                         as expert_id,
  e.slug                       as expert_slug,
  e.name                       as expert_name,
  e.kind                       as expert_kind,
  o.name                       as outlet_name,
  l.slug                       as league,
  s.year                       as season,
  count(*)                     as games_graded,
  count(*) filter (where gr.su_result = 'win')   as su_wins,
  count(*) filter (where gr.su_result = 'loss')  as su_losses,
  count(*) filter (where gr.su_result = 'push')  as su_pushes,
  round(count(*) filter (where gr.su_result = 'win')::numeric
        / nullif(count(*) filter (where gr.su_result in ('win', 'loss')), 0), 4) as su_pct,
  count(*) filter (where gr.ats_result = 'win')  as ats_wins,
  count(*) filter (where gr.ats_result = 'loss') as ats_losses,
  count(*) filter (where gr.ats_result = 'push') as ats_pushes,
  round(count(*) filter (where gr.ats_result = 'win')::numeric
        / nullif(count(*) filter (where gr.ats_result in ('win', 'loss')), 0), 4) as ats_pct,
  count(gr.margin_error)       as margin_graded,
  round(avg(gr.margin_error), 2) as margin_mae,
  count(gr.brier)              as brier_graded,
  round(avg(gr.brier), 4)      as brier
from public.grades gr
join public.predictions p on p.id = gr.prediction_id
join public.experts e on e.id = p.expert_id
left join public.outlets o on o.id = e.outlet_id
join public.games g on g.id = gr.game_id
join public.seasons s on s.id = g.season_id
join public.leagues l on l.id = s.league_id
group by e.id, e.slug, e.name, e.kind, o.name, l.slug, s.year;

grant select on public.leaderboard to anon, authenticated;
