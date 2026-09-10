import { ChevronDown } from 'lucide-react';
import { CHECK_CATALOG, TOTAL_CHECK_COUNT } from '@/lib/checkCatalog';
import { Badge } from '@/components/ui/badge';

/**
 * Six collapsed, per-category groups — native <details>/<summary> rather
 * than a Radix accordion, so it's keyboard-operable and announced to
 * screen readers for free, and still works with JS disabled. The
 * group-open: chevron rotation mirrors the pattern already used for the
 * in-app check list (components/ScanChecks.tsx), just driven by the
 * browser's own open state instead of React state.
 */
export function ChecksExplorer() {
  return (
    <div className="max-w-3xl">
      <div className="flex flex-col divide-y divide-border/60 border-y border-border/60">
        {CHECK_CATALOG.map((group) => (
          <details key={group.category} className="group">
            <summary className="flex cursor-pointer list-none items-center gap-4 py-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background [&::-webkit-details-marker]:hidden">
              <ChevronDown className="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" />
              <span className="font-display text-base font-semibold">{group.category}</span>
              <span className="font-mono text-sm text-editorial">{group.weight}</span>
              <span className="ml-auto font-mono text-xs uppercase tracking-[0.15em] text-muted-foreground">
                {group.checks.length} checks
              </span>
            </summary>
            <ul className="flex flex-col gap-2 pb-4 pl-8">
              {group.checks.map((check) => (
                <li key={check.id} className="flex items-center gap-2 text-sm text-foreground">
                  <span>{check.title}</span>
                  {check.tool && (
                    <Badge variant="outline" className="text-editorial">
                      {check.tool}
                    </Badge>
                  )}
                </li>
              ))}
            </ul>
          </details>
        ))}
      </div>
      <p className="mt-6 text-sm text-muted-foreground">
        Every one of the {TOTAL_CHECK_COUNT} checks reports passed, flagged,
        skipped, or errored, with a reason — a check the tooling couldn&rsquo;t
        complete is <span className="font-mono text-foreground">errored</span>,
        never <span className="font-mono text-foreground">passed</span>.
      </p>
    </div>
  );
}
