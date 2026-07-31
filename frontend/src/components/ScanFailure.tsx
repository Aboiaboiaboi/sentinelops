import { TriangleAlert } from 'lucide-react';
import { Alert, AlertDescription } from '@/components/ui/alert';
import type { ScanSummary } from '@/types/scan';

/**
 * Why a scan failed, and what to do about it.
 *
 * Older scans failed before any of this was recorded, so the generic sentence
 * stays as the fallback — a failed scan must always say *something*, and an
 * empty alert would be worse than a vague one.
 */
export function ScanFailure({ scan }: { scan: ScanSummary }) {
  if (scan.status !== 'failed') return null;

  return (
    <Alert variant="destructive">
      <TriangleAlert />
      <AlertDescription>
        <div className="space-y-2">
          <p>{scan.error_detail ?? 'This scan failed before it could produce a score.'}</p>
          {scan.error_hint && (
            <p className="text-sm opacity-90">
              <span className="font-medium">What to try: </span>
              {scan.error_hint}
            </p>
          )}
        </div>
      </AlertDescription>
    </Alert>
  );
}
