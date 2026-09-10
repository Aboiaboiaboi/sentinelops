import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

/**
 * A number set in the accent — a metric value, a count, a category weight, a
 * score delta at rest. The one place the honey-gold reaches a figure.
 *
 * NOT for a number whose colour already carries meaning: the gauge score, a
 * severity impact, a pending/failed state, a status badge. Gold there would
 * paint over a signal. If in doubt, it doesn't get the accent.
 */
export function Figure({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        'font-display font-semibold tabular-nums tracking-tight text-editorial',
        className,
      )}
    >
      {children}
    </span>
  );
}
