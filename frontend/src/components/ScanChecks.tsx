import { useState } from 'react';
import { AlertTriangle, Check, ChevronDown, Minus, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { categoryLabel } from '@/lib/categories';
import { cn } from '@/lib/utils';
import { useChecks } from '@/hooks/useFindings';
import type { CheckOutcome, CheckResult } from '@/types/check';

const OUTCOME_ICON = {
  passed: Check,
  failed: X,
  skipped: Minus,
  errored: AlertTriangle,
} as const;

const OUTCOME_CLASS: Record<CheckOutcome, string> = {
  passed: 'text-scan-completed',
  failed: 'text-severity-high',
  skipped: 'text-muted-foreground',
  // Amber rather than grey: an errored check is not a quiet non-event. It is a
  // question this scan failed to answer, and the reader should notice.
  errored: 'text-severity-medium',
};

/** Grouped by category, in the order the API returned them. */
function groupByCategory(checks: CheckResult[]): [string, CheckResult[]][] {
  const groups = new Map<string, CheckResult[]>();
  for (const check of checks) {
    const existing = groups.get(check.category);
    if (existing) existing.push(check);
    else groups.set(check.category, [check]);
  }
  return [...groups.entries()];
}

function summarise(checks: CheckResult[]): string {
  const count = (outcome: CheckOutcome) => checks.filter((c) => c.outcome === outcome).length;
  const parts = [`${count('passed')} passed`];
  // Only states actually present are named, so a healthy category reads
  // "5 passed" rather than trailing two zeroes nobody needs.
  for (const outcome of ['failed', 'skipped', 'errored'] as const) {
    const total = count(outcome);
    if (total) parts.push(`${total} ${outcome}`);
  }
  return parts.join(' · ');
}

/**
 * What the scan actually examined, behind a disclosure.
 *
 * This is what a score alone cannot say: that a category earned full marks
 * *because these checks passed*, and that a check which did not apply was
 * skipped rather than quietly counted as fine. Collapsed by default because it
 * is detail — the score and the findings are the headline.
 */
export function ScanChecks({ scanId }: { scanId: string }) {
  const [open, setOpen] = useState(false);
  const { data: checks, isPending, isError } = useChecks(scanId, open);

  return (
    <section className="rounded-lg border">
      <Button
        variant="ghost"
        className="h-auto w-full justify-between px-4 py-3 font-normal"
        aria-expanded={open}
        onClick={() => setOpen((wasOpen) => !wasOpen)}
      >
        <span className="text-sm font-medium">What was checked</span>
        <ChevronDown
          className={cn('size-4 text-muted-foreground transition-transform', open && 'rotate-180')}
          aria-hidden="true"
        />
      </Button>

      {open && (
        <div className="border-t px-4 py-3">
          {isPending && <Skeleton className="h-24 w-full" />}

          {isError && (
            <p role="alert" className="text-sm text-destructive">
              The checks for this scan could not be loaded.
            </p>
          )}

          {checks && checks.length === 0 && (
            <p className="text-sm text-muted-foreground">
              This scan recorded no checks — it failed before any category ran, or predates
              check-level reporting.
            </p>
          )}

          {checks && checks.length > 0 && (
            <div className="space-y-4">
              {groupByCategory(checks).map(([category, categoryChecks]) => (
                <div key={category}>
                  <div className="mb-1.5 flex items-baseline justify-between gap-2">
                    <h4 className="text-sm font-medium">{categoryLabel(category)}</h4>
                    <span className="text-xs text-muted-foreground">
                      {summarise(categoryChecks)}
                    </span>
                  </div>
                  <ul className="space-y-1">
                    {categoryChecks.map((check) => {
                      const Icon = OUTCOME_ICON[check.outcome];
                      return (
                        <li key={check.id} className="flex items-start gap-2 text-sm">
                          <Icon
                            className={cn('mt-0.5 size-3.5 shrink-0', OUTCOME_CLASS[check.outcome])}
                            aria-hidden="true"
                          />
                          <span className="min-w-0">
                            <span
                              className={cn(
                                check.outcome === 'skipped' && 'text-muted-foreground',
                              )}
                            >
                              {check.title}
                            </span>
                            {/* The reason is the difference between "we looked
                                and it was fine" and "this did not apply". */}
                            {check.reason && (
                              <span className="text-muted-foreground"> — {check.reason}</span>
                            )}
                            <span className="sr-only"> ({check.outcome})</span>
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
