import Link from "next/link";
import { LEAGUES, METRICS, type League, type Metric } from "@/lib/leaderboard";

function Pill({ href, active, children }: { href: string; active: boolean; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      aria-current={active ? "page" : undefined}
      className={
        "rounded-full border px-3 py-1 text-sm transition-colors " +
        (active
          ? "border-neutral-900 bg-neutral-900 text-white dark:border-neutral-100 dark:bg-neutral-100 dark:text-neutral-900"
          : "border-neutral-300 text-neutral-700 hover:border-neutral-500 dark:border-neutral-700 dark:text-neutral-300")
      }
    >
      {children}
    </Link>
  );
}

export function Filters({ league, metric }: { league: League; metric: Metric }) {
  const href = (l: League, m: Metric) => `/?league=${l}&metric=${m}`;
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <nav aria-label="League" className="flex flex-wrap gap-2">
        {LEAGUES.map((l) => (
          <Pill key={l.slug} href={href(l.slug, metric)} active={l.slug === league}>{l.label}</Pill>
        ))}
      </nav>
      <nav aria-label="Rank by" className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-neutral-500">Rank by</span>
        {METRICS.map((m) => (
          <Pill key={m.slug} href={href(league, m.slug)} active={m.slug === metric}>{m.label}</Pill>
        ))}
      </nav>
    </div>
  );
}
