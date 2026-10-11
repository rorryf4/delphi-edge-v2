import "server-only";
import { supabase } from "./supabase";

export const LEAGUES = [
  { slug: "nfl", label: "NFL" },
  { slug: "cfb", label: "College" },
] as const;
export type League = (typeof LEAGUES)[number]["slug"];

export const METRICS = [
  { slug: "su", label: "Straight up", better: "higher" },
  { slug: "ats", label: "Against the spread", better: "higher" },
  { slug: "margin", label: "Margin error", better: "lower" },
  { slug: "brier", label: "Brier score", better: "lower" },
] as const;
export type Metric = (typeof METRICS)[number]["slug"];

export type ExpertKind = "human" | "model" | "market";

export type Row = {
  slug: string;
  name: string;
  kind: ExpertKind;
  outlet: string | null;
  gamesGraded: number;
  su: { wins: number; losses: number; pushes: number; pct: number | null; n: number };
  ats: { wins: number; losses: number; pushes: number; pct: number | null; n: number };
  margin: { mae: number | null; n: number };
  brier: { score: number | null; n: number };
};

type ViewRow = {
  expert_slug: string;
  expert_name: string;
  expert_kind: ExpertKind;
  outlet_name: string | null;
  games_graded: number;
  su_wins: number; su_losses: number; su_pushes: number; su_pct: number | null;
  ats_wins: number; ats_losses: number; ats_pushes: number; ats_pct: number | null;
  margin_graded: number; margin_mae: number | null;
  brier_graded: number; brier: number | null;
};

const num = (v: unknown) => (v === null || v === undefined ? null : Number(v));

/** The value a metric is ranked on, or null if the expert has none. */
export function metricValue(row: Row, metric: Metric): number | null {
  switch (metric) {
    case "su": return row.su.n ? row.su.pct : null;
    case "ats": return row.ats.n ? row.ats.pct : null;
    case "margin": return row.margin.n ? row.margin.mae : null;
    case "brier": return row.brier.n ? row.brier.score : null;
  }
}

/** Games behind a metric. */
export function metricCount(row: Row, metric: Metric): number {
  return { su: row.su.n, ats: row.ats.n, margin: row.margin.n, brier: row.brier.n }[metric];
}

export async function currentSeason(): Promise<number> {
  const { data, error } = await supabase()
    .from("seasons").select("year").order("year", { ascending: false }).limit(1);
  if (error) throw error;
  return data?.[0]?.year ?? new Date().getFullYear();
}

export async function loadLeaderboard(league: League, season: number, metric: Metric) {
  const db = supabase();
  const [board, fixed] = await Promise.all([
    db.from("leaderboard").select("*").eq("league", league).eq("season", season),
    // The model and market are always listed, even before they have grades.
    db.from("experts").select("slug, name, kind").in("kind", ["model", "market"]).eq("active", true),
  ]);
  if (board.error) throw board.error;
  if (fixed.error) throw fixed.error;

  const rows = new Map<string, Row>();
  for (const r of (board.data ?? []) as ViewRow[]) {
    rows.set(r.expert_slug, {
      slug: r.expert_slug, name: r.expert_name, kind: r.expert_kind, outlet: r.outlet_name,
      gamesGraded: Number(r.games_graded),
      su: { wins: +r.su_wins, losses: +r.su_losses, pushes: +r.su_pushes, pct: num(r.su_pct),
            n: +r.su_wins + +r.su_losses + +r.su_pushes },
      ats: { wins: +r.ats_wins, losses: +r.ats_losses, pushes: +r.ats_pushes, pct: num(r.ats_pct),
             n: +r.ats_wins + +r.ats_losses + +r.ats_pushes },
      margin: { mae: num(r.margin_mae), n: Number(r.margin_graded) },
      brier: { score: num(r.brier), n: Number(r.brier_graded) },
    });
  }
  for (const e of fixed.data ?? []) {
    if (!rows.has(e.slug)) {
      rows.set(e.slug, {
        slug: e.slug, name: e.name, kind: e.kind as ExpertKind, outlet: null, gamesGraded: 0,
        su: { wins: 0, losses: 0, pushes: 0, pct: null, n: 0 },
        ats: { wins: 0, losses: 0, pushes: 0, pct: null, n: 0 },
        margin: { mae: null, n: 0 },
        brier: { score: null, n: 0 },
      });
    }
  }

  const lowerIsBetter = METRICS.find((m) => m.slug === metric)!.better === "lower";
  const sorted = [...rows.values()].sort((a, b) => {
    const va = metricValue(a, metric), vb = metricValue(b, metric);
    if (va === null && vb === null) return b.gamesGraded - a.gamesGraded || a.name.localeCompare(b.name);
    if (va === null) return 1;
    if (vb === null) return -1;
    if (va !== vb) return lowerIsBetter ? va - vb : vb - va;
    return metricCount(b, metric) - metricCount(a, metric);
  });

  // Rank only experts who have the metric; equal values share a rank.
  let rank = 0, prev: number | null = null;
  const ranked = sorted.map((row, i) => {
    const v = metricValue(row, metric);
    if (v === null) return { row, rank: null as number | null };
    if (v !== prev) { rank = i + 1; prev = v; }
    return { row, rank };
  });
  return ranked;
}
