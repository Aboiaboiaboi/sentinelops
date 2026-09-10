import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

/**
 * A hairline-divided list — one bordered container, rows separated by a rule
 * rather than each row being its own boxed `Card`. The app's equivalent of
 * what `ChecksExplorer` looks like on the marketing side.
 *
 * `RowList` is the container; `Row` is one line. `Row` is presentational
 * only — put the `<li>`/`<Link>`/`<button>` semantics on or inside it.
 */
export function RowList({
  children,
  className,
  as: Tag = 'div',
}: {
  children: ReactNode;
  className?: string;
  as?: 'div' | 'ul';
}) {
  return (
    <Tag
      className={cn(
        'divide-y divide-border/60 overflow-hidden rounded-lg border border-border/60',
        className,
      )}
    >
      {children}
    </Tag>
  );
}

export function Row({
  children,
  className,
  interactive = false,
  as: Tag = 'div',
}: {
  children: ReactNode;
  className?: string;
  /** Adds a hover tint — set when the whole row is a link. */
  interactive?: boolean;
  as?: 'div' | 'li';
}) {
  return (
    <Tag
      className={cn(
        'flex items-center justify-between gap-4 bg-card/40 px-4 py-3.5',
        interactive && 'transition-colors hover:bg-card/70',
        className,
      )}
    >
      {children}
    </Tag>
  );
}
