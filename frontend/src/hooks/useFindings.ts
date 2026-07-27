import { useQuery } from '@tanstack/react-query';
import { listFindings } from '@/api/findings';
import { USE_FIXTURES, store } from '@/lib/fixtures';
import { SEVERITY_ORDER, type Finding } from '@/types/finding';

export const findingKeys = {
  forScan: (scanId: string) => ['scans', scanId, 'findings'] as const,
};

/**
 * Findings only exist once the worker has written them, so this stays disabled
 * until the scan has actually finished — otherwise every poll cycle would fire
 * a second request that is guaranteed to return an empty list.
 */
export function useFindings(scanId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: findingKeys.forScan(scanId ?? ''),
    queryFn: () =>
      USE_FIXTURES ? store.listFindings(scanId as string) : listFindings(scanId as string),
    enabled: Boolean(scanId) && enabled,
    select: (findings: Finding[]) =>
      [...findings].sort(
        (a, b) =>
          SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity] ||
          b.score_impact - a.score_impact,
      ),
  });
}
