-- DelphiEdge v2: head-to-head comparison on shared games only.
--
-- Rule: any comparison between two experts uses only the games both have
-- graded, and each metric only the games where BOTH have that metric (e.g.
-- Brier only where both gave a win probability). Every count is returned so
-- the page can say how many games each number rests on.
--
-- Usage: select * from head_to_head('market', 'some-writer', 'nfl', 2026);

create or replace function public.head_to_head(
  expert_a text, expert_b text, league_slug text default null, season_year int default null)
returns table (
  expert_slug   text,
  shared_games  bigint,
  su_graded     bigint, su_wins bigint, su_losses bigint, su_pushes bigint, su_pct numeric,
  ats_graded    bigint, ats_wins bigint, ats_losses bigint, ats_pushes bigint, ats_pct numeric,
  margin_graded bigint, margin_mae numeric,
  brier_graded  bigint, brier numeric
)
language sql stable security invoker as $$
  with graded as (
    select e.slug, gr.*
    from public.grades gr
    join public.predictions p on p.id = gr.prediction_id
    join public.experts e on e.id = p.expert_id
    join public.games g on g.id = gr.game_id
    join public.seasons s on s.id = g.season_id
    join public.leagues l on l.id = s.league_id
    where e.slug in (expert_a, expert_b)
      and (league_slug is null or l.slug = league_slug)
      and (season_year is null or s.year = season_year)
  ),
  pairs as (
    select a.game_id,
           a.su_result a_su, b.su_result b_su, a.ats_result a_ats, b.ats_result b_ats,
           a.margin_error a_margin, b.margin_error b_margin, a.brier a_brier, b.brier b_brier
    from graded a join graded b on b.game_id = a.game_id
    where a.slug = expert_a and b.slug = expert_b
  ),
  sides as (
    select expert_a as slug, game_id,
           case when b_su is not null then a_su end su,
           case when b_ats is not null then a_ats end ats,
           case when b_margin is not null then a_margin end margin,
           case when b_brier is not null then a_brier end brier
    from pairs
    union all
    select expert_b, game_id,
           case when a_su is not null then b_su end,
           case when a_ats is not null then b_ats end,
           case when a_margin is not null then b_margin end,
           case when a_brier is not null then b_brier end
    from pairs
  )
  select x.slug,
         (select count(*) from pairs),
         count(su),
         count(*) filter (where su = 'win'), count(*) filter (where su = 'loss'),
         count(*) filter (where su = 'push'),
         round(count(*) filter (where su = 'win')::numeric
               / nullif(count(*) filter (where su in ('win', 'loss')), 0), 4),
         count(ats),
         count(*) filter (where ats = 'win'), count(*) filter (where ats = 'loss'),
         count(*) filter (where ats = 'push'),
         round(count(*) filter (where ats = 'win')::numeric
               / nullif(count(*) filter (where ats in ('win', 'loss')), 0), 4),
         count(margin), round(avg(margin), 2),
         count(brier), round(avg(brier), 4)
  from (select expert_a as slug union all select expert_b) x
  left join sides s on s.slug = x.slug
  group by x.slug
  order by (x.slug = expert_b);
$$;
