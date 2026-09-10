import type { ElementType, ReactNode } from 'react';
import { cn } from '@/lib/utils';

/**
 * The mono, uppercase, wide-tracked kicker that sits above a heading. Started
 * on the marketing pages as the cheapest way to break their centered-sameness;
 * now also carries the "quiet cues" look into the app — section labels, the
 * caption over a page title, metric labels.
 *
 * Renders a <p> by default; pass `as="span"` to drop it inline into a heading
 * row without nesting block elements.
 */
export function Eyebrow({
  children,
  className,
  as: Tag = 'p',
}: {
  children: ReactNode;
  className?: string;
  as?: ElementType;
}) {
  return (
    <Tag
      className={cn(
        'font-mono text-xs font-medium uppercase tracking-[0.2em] text-editorial',
        className,
      )}
    >
      {children}
    </Tag>
  );
}
