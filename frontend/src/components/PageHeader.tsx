import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { Eyebrow } from '@/components/Eyebrow';

/**
 * The opening row every app page shares: an optional mono kicker, the page
 * title, an optional caption, and right-aligned actions. Kept deliberately
 * quiet — `font-display` with tight tracking but no size jump — so the app
 * reads as the marketing site's tool, not its cover.
 *
 * Pass `children` for the normal case (rendered as the page's single `h1`).
 * Pass `titleSlot` when the title is itself interactive and owns its own
 * heading element — `ScanName` does.
 */
export function PageHeader({
  children,
  titleSlot,
  eyebrow,
  caption,
  actions,
  className,
}: {
  children?: ReactNode;
  titleSlot?: ReactNode;
  eyebrow?: ReactNode;
  caption?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn('flex flex-wrap items-start justify-between gap-4', className)}>
      <div className="min-w-0 space-y-1">
        {eyebrow && <Eyebrow className="text-muted-foreground">{eyebrow}</Eyebrow>}
        {titleSlot ?? (
          <h1 className="font-display text-2xl font-semibold tracking-tight">{children}</h1>
        )}
        {caption && <p className="text-sm text-muted-foreground">{caption}</p>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
