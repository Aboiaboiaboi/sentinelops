import type { ElementType, ReactNode } from 'react';
import { cn } from '@/lib/utils';

/**
 * The mono uppercase label that opens a section within a page — "Scan
 * history", "Findings", a category name. Replaces the plain
 * `text-sm font-medium text-muted-foreground` heading the app used before,
 * and matches the eyebrow treatment the marketing pages use for the same job.
 *
 * Renders a real `<h2>`, so it keeps the page's heading outline intact. Pass
 * `id` to wire up an `aria-labelledby` on the owning `<section>`.
 */
export function SectionLabel({
  children,
  id,
  as: Heading = 'h2',
  count,
  trailing,
  className,
}: {
  children: ReactNode;
  id?: string;
  as?: ElementType;
  count?: ReactNode;
  trailing?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        'mb-3 flex items-baseline gap-3 border-b border-border/60 pb-2',
        className,
      )}
    >
      <Heading
        id={id}
        className="font-mono text-xs font-medium uppercase tracking-[0.15em] text-muted-foreground"
      >
        {children}
      </Heading>
      {count != null && (
        <span className="font-mono text-xs text-muted-foreground/70 tabular-nums">{count}</span>
      )}
      {trailing && <div className="ml-auto shrink-0">{trailing}</div>}
    </div>
  );
}
