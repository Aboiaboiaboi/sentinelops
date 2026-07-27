import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import type { ScanStatus } from '@/types/scan';

const STATUS_LABEL: Record<ScanStatus, string> = {
  pending: 'Queued',
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
};

const STATUS_CLASS: Record<ScanStatus, string> = {
  pending: 'bg-muted text-muted-foreground',
  running: 'bg-scan-pending/15 text-foreground border-scan-pending/40',
  completed: 'bg-scan-completed/15 text-foreground border-scan-completed/40',
  failed: 'bg-destructive/15 text-destructive border-destructive/40',
};

export function ScanStatusBadge({
  status,
  className,
}: {
  status: ScanStatus;
  className?: string;
}) {
  const inFlight = status === 'pending' || status === 'running';

  return (
    <Badge variant="outline" className={cn(STATUS_CLASS[status], className)}>
      <span
        className={cn(
          'mr-1.5 size-1.5 rounded-full bg-current',
          inFlight && 'bar-pending',
        )}
      />
      {STATUS_LABEL[status]}
    </Badge>
  );
}
