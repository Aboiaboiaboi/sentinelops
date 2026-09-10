import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface SectionHeadingProps {
  /** Two-digit section number, e.g. "01". Rendered in the editorial accent. */
  number?: string;
  eyebrow?: string;
  children: ReactNode;
  /** Follow-on paragraph under the heading. */
  lede?: ReactNode;
  className?: string;
}

/**
 * The numbered editorial section opener — a small accent numeral, a hairline
 * rule, then an oversized heading — replacing the repeated centered `<h2>`
 * every section used to have. Left-aligned on purpose: an editorial layout
 * reads down a left edge, it doesn't re-centre every block.
 *
 * Heading level is `h2` — pages keep one `h1` in their hero and never skip
 * from there, so this is always the right level for a top-level section.
 */
export function SectionHeading({
  number,
  eyebrow,
  children,
  lede,
  className,
}: SectionHeadingProps) {
  return (
    <div className={cn('max-w-2xl', className)}>
      {(number || eyebrow) && (
        <div className="mb-4 flex items-center gap-4">
          {number && (
            <span className="font-mono text-sm font-medium text-editorial" aria-hidden="true">
              {number}
            </span>
          )}
          <span className="h-px flex-1 bg-border" aria-hidden="true" />
          {eyebrow && (
            <span className="font-mono text-xs uppercase tracking-[0.2em] text-muted-foreground">
              {eyebrow}
            </span>
          )}
        </div>
      )}
      <h2 className="text-balance font-display text-3xl font-semibold tracking-tight sm:text-4xl">
        {children}
      </h2>
      {lede && <p className="mt-3 text-lg text-muted-foreground">{lede}</p>}
    </div>
  );
}
