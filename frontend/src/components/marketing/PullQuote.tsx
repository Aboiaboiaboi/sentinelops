import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

/**
 * A large-type callout for lifting one real line out of the body text — the
 * kind of thing a magazine sets in 30pt in the margin. Uses a `<figure>` so
 * it's semantically an aside, not a heading, and can't disturb the page's
 * heading hierarchy.
 *
 * `mark` adds a large decorative opening quote glyph above the text (hidden
 * from assistive tech). Off by default — the accent rule down the left edge
 * already reads as a quote, and the glyph only earns its space where the
 * block would otherwise be visually quiet.
 */
export function PullQuote({
  children,
  cite,
  className,
  mark = false,
}: {
  children: ReactNode;
  cite?: string;
  className?: string;
  mark?: boolean;
}) {
  return (
    <figure className={cn('border-l-2 border-editorial pl-6 sm:pl-8', className)}>
      {mark && (
        <span
          className="mb-1 block font-display text-5xl leading-none text-editorial"
          aria-hidden="true"
        >
          &ldquo;
        </span>
      )}
      <blockquote className="text-balance font-display text-2xl font-medium leading-snug tracking-tight sm:text-3xl">
        {children}
      </blockquote>
      {cite && (
        <figcaption className="mt-3 font-mono text-xs uppercase tracking-[0.2em] text-muted-foreground">
          {cite}
        </figcaption>
      )}
    </figure>
  );
}
