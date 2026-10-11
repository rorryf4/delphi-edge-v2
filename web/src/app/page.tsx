import {
  LEAGUES, METRICS, currentSeason, loadLeaderboard,
  type League, type Metric, type Row,
} from "@/lib/leaderboard";
import { Filters } from "./_components/Filters";

export const dynamic = "force-dynamic";

const pct = (v: number | null) => (v === null ? "–" : `${(v * 100).toFixed(1)}%`);
const games = (n: number) => `${n} ${n === 1 ? "game" : "games"}`;

function Record({ r }: { r: Row["su"] }) {
  if (!r.n) return <Empty />;
  return (
    <>
      <div className="font-medium tabular-nums">{pct(r.pct)}</div>
      <div className="text-xs text-neutral-500 tabular-nums">
        {r.wins}-{r.losses}{r.pushes ? `-${r.pushes}` : ""} · {games(r.n)}
      </div>
    </>
  );
}

function Value({ value, n, digits }: { value: number | null; n: number; digits: number }) {
  if (!n || value === null) return <Empty />;
  return (
    <>
      <div className="font-medium tabular-nums">{value.toFixed(digits)}</div>
      <div className="text-xs text-neutral-500 tabular-nums">{games(n)}</div>
    </>
  );
}

const Empty = () => <span className="text-neutral-400">–</span>;
const NotApplicable = ({ title }: { title: string }) => (
  <span className="text-neutral-400" title={title}>N/A</span>
);

const KIND_LABEL = { human: "Pundit", model: "Model", market: "Benchmark" } as const;

export default async function LeaderboardPage({
  searchParams,
}: {
  searchParams: Promise<{ league?: string; metric?: string }>;
}) {
  const params = await searchParams;
  const league = (LEAGUES.some((l) => l.slug === params.league) ? params.league : "nfl") as League;
  const metric = (METRICS.some((m) => m.slug === params.metric) ? params.metric : "su") as Metric;
  const season = await currentSeason();
  const rows = await loadLeaderboard(league, season, metric);
  const leagueLabel = LEAGUES.find((l) => l.slug === league)!.label;
  const metricInfo = METRICS.find((m) => m.slug === metric)!;

  const th = (m: Metric | null, label: string) => (
    <th
      scope="col"
      className={
        "px-3 py-2 text-left text-xs font-medium uppercase tracking-wide " +
        (m === metric ? "text-neutral-900 dark:text-neutral-100" : "text-neutral-500")
      }
    >
      {label}
    </th>
  );
  const td = (m: Metric | null) =>
    "px-3 py-3 align-top " + (m === metric ? "bg-neutral-50 dark:bg-neutral-900" : "");

  return (
    <main className="mx-auto max-w-5xl px-4 py-10 sm:py-14">
      <header className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight">DelphiEdge leaderboard</h1>
        <p className="mt-2 max-w-2xl text-neutral-600 dark:text-neutral-400">
          Every prediction graded in public. {leagueLabel}, {season} season, ranked by{" "}
          {metricInfo.label.toLowerCase()} ({metricInfo.better} is better).
        </p>
      </header>

      <Filters league={league} metric={metric} />

      <div className="mt-6 overflow-x-auto rounded-lg border border-neutral-200 dark:border-neutral-800">
        <table className="w-full min-w-[720px] border-collapse text-sm">
          <thead className="border-b border-neutral-200 dark:border-neutral-800">
            <tr>
              {th(null, "#")}
              {th(null, "Expert")}
              {th("su", "Straight up")}
              {th("ats", "Against spread")}
              {th("margin", "Margin error")}
              {th("brier", "Brier")}
            </tr>
          </thead>
          <tbody>
            {rows.map(({ row, rank }) => (
              <tr key={row.slug} className="border-b border-neutral-100 last:border-0 dark:border-neutral-900">
                <td className="px-3 py-3 align-top tabular-nums text-neutral-500">{rank ?? "–"}</td>
                <td className="px-3 py-3 align-top">
                  <div className="font-medium">{row.name}</div>
                  <div className="text-xs text-neutral-500">
                    {row.outlet ?? KIND_LABEL[row.kind]}
                    {row.gamesGraded === 0 ? " · no graded predictions yet" : ""}
                  </div>
                </td>
                <td className={td("su")}><Record r={row.su} /></td>
                <td className={td("ats")}>
                  {row.kind === "market"
                    ? <NotApplicable title="The market sets the line, so it has no record against it." />
                    : <Record r={row.ats} />}
                </td>
                <td className={td("margin")}>
                  <Value value={row.margin.mae} n={row.margin.n} digits={1} />
                </td>
                <td className={td("brier")}>
                  {row.kind === "human"
                    ? <NotApplicable title="Pundits do not publish win probabilities." />
                    : <Value value={row.brier.score} n={row.brier.n} digits={3} />}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <section className="mt-6 space-y-2 text-sm text-neutral-600 dark:text-neutral-400">
        <p>
          Each number shows how many games it is based on. Experts are graded on different
          games, so a head-to-head comparison uses only the games both experts picked.
        </p>
        <p>
          Straight up and against the spread count pushes in the record but not the percentage.
          Picks against the spread are graded against the line the expert quoted, or the
          closing line if none was quoted. Margin error is the average points off the final
          margin. Brier scores win probabilities from 0 (perfect) to 1; a coin flip scores 0.25.
        </p>
        <p>
          The market benchmark is the closing line with the bookmaker&apos;s margin removed. It
          has no record against the spread, and games without closing moneylines are left out of
          its Brier score.
        </p>
      </section>
    </main>
  );
}
