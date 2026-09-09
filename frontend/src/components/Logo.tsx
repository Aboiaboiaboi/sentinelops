import { ShieldCheck } from 'lucide-react';
import { cn } from '@/lib/utils';

const ICON_SIZE: Record<LogoSize, string> = {
  sm: 'size-6',
  md: 'size-8',
  lg: 'size-16 sm:size-20',
};

const TEXT_SIZE: Record<LogoSize, string> = {
  sm: 'text-base',
  md: 'text-xl',
  lg: 'text-4xl sm:text-5xl',
};

export type LogoSize = 'sm' | 'md' | 'lg';

interface LogoProps {
  size?: LogoSize;
  /** False for icon-only contexts (e.g. a favicon-style mark). */
  showWordmark?: boolean;
  className?: string;
}

/**
 * The one place that knows what the SentinelOps mark looks like. Previously
 * a `ShieldCheck` + "SentinelOps" pair was hand-typed at size-5 in two spots
 * in AppLayout.tsx — now there's one component with a real size scale, so
 * the marketing pages can render it large without a third copy drifting out
 * of sync with the other two.
 */
export function Logo({ size = 'md', showWordmark = true, className }: LogoProps) {
  return (
    <span className={cn('inline-flex items-center gap-2', className)}>
      <ShieldCheck className={cn(ICON_SIZE[size], 'shrink-0 text-primary-bright')} />
      {showWordmark && (
        <span className={cn('font-display font-semibold tracking-tight', TEXT_SIZE[size])}>
          SentinelOps
        </span>
      )}
    </span>
  );
}
