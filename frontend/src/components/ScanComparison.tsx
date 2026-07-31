import { ArrowDown, ArrowUp, Minus } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { categoryLabel } from '@/lib/categories';
import { cn } from '@/lib/utils';
import { useComparison } from '@/hooks/useFindings';
import type { CategoryDelta } from '@/types/check';

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString();
}

/** Signed, so +6 and −6 are distinguishable at a glance. */
function signed(delta: number): string {
  return delta > 0 ? `+${delta}` : `${delta}`;
}

function deltaClass(delta: number): string {
  if (delta > 0) return 'text-scan-completed';
  if (delta < 0) return 'text-severity-high';
  return 'text-muted-foreground';
}

function DeltaBadge({ delta }: { delta: number }) {
  const Icon = delta > 0 ? ArrowUp : delta < 0 ? ArrowDown : Minus;
  return (
    <span className={cn('inline-flex items-center gap-1 tabular-nums', deltaClass(delta))}>
      <Icon className="size-3.5" aria-hidden="true" />
      {signed(delta)}
    </span>
  );
}

/** Categories worth showing: something moved, or something stopped being assessed. */
function interesting(categories: CategoryDelta[]): CategoryDelta[] {
  return categories.filter((c) => c.delta === null || c.delta !== 0);
}

/**
 * How this scan compares to the previous one.
 *
 * Renders nothing when there is no earlier scan — the first scan of a project
 * is a normal state, not an empty slot worth explaining.
 */
export function ScanComparison({ scanId, enabled }: { scanId: string; enabled: boolean }) {
  const { data: comparison, isPending, isError } = useComparison(scanId, enabled);

  if (!enabled || isError) return null;
  if (isPending) return <Skeleton className="h-16 w-full" />;
  if (!comparison || comparison.previous_scan_id === null) return null;

  const when = comparison.previous_created_at
    ? formatDate(comparison.previous_created_at)
    : 'the previous scan';

  // An earlier scan exists but the comparison would mislead. Saying so beats
  // rendering a number that measures the wrong thing.
  if (!comparison.comparable) {
    return (
      <Card>
        <CardContent className="space-y-1 py-4">
          <p className="text-sm font-medium">Not comparable with the previous scan</p>
          <p className="text-sm text-muted-foreground">{comparison.reason}</p>
        </CardContent>
      </Card>
    );
  }

  const moved = interesting(comparison.categories);

  return (
    <Card>
      <CardContent className="space-y-3 py-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <p className="text-sm">
            <span className="font-medium">Compared with {when}</span>
            {comparison.previous_score !== null && (
              <span className="text-muted-foreground"> · was {comparison.previous_score}/100</span>
            )}
          </p>
          {comparison.score_delta !== null && (
            <span className="text-sm font-medium">
              <DeltaBadge delta={comparison.score_delta} />
            </span>
          )}
        </div>

        {comparison.score_delta === 0 && moved.length === 0 && (
          <p className="text-sm text-muted-foreground">Nothing changed since the last scan.</p>
        )}

        {moved.length > 0 && (
          <ul className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
            {moved.map((category) => (
              <li key={category.category} className="inline-flex items-center gap-1.5">
                <span className="text-muted-foreground">{categoryLabel(category.category)}</span>
                {category.delta === null ? (
                  // Not a drop: the category stopped (or started) being
                  // assessed, and showing minus its weight would accuse the
                  // repository of a regression it did not have.
                  <span className="text-muted-foreground">
                    {category.current === null ? 'no longer assessed' : 'newly assessed'}
                  </span>
                ) : (
                  <DeltaBadge delta={category.delta} />
                )}
              </li>
            ))}
          </ul>
        )}

        {comparison.checks.length > 0 && (
          <ul className="space-y-1 border-t pt-2 text-sm">
            {comparison.checks.map((change) => (
              <li key={change.id} className="flex flex-wrap items-baseline gap-x-2">
                <span>{change.title}</span>
                <span className="text-xs text-muted-foreground">
                  {change.previous_outcome} → {change.current_outcome}
                </span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
