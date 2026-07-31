import { useQuery } from '@tanstack/react-query';
import { getComparison, listChecks, listFindings } from '@/api/findings';
import { USE_FIXTURES, store } from '@/lib/fixtures';
import { SEVERITY_ORDER, type Finding } from '@/types/finding';

export const findingKeys = {
  forScan: (scanId: string) => ['scans', scanId, 'findings'] as const,
  checksForScan: (scanId: string) => ['scans', scanId, 'checks'] as const,
  comparisonForScan: (scanId: string) => ['scans', scanId, 'comparison'] as const,
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

/**
 * Check outcomes, fetched only once somebody asks for them.
 *
 * `enabled` is the whole point: this is detail behind a disclosure, so it must
 * not ride along with every poll of a running scan.
 */
export function useChecks(scanId: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: findingKeys.checksForScan(scanId ?? ''),
    queryFn: () =>
      USE_FIXTURES ? store.listChecks(scanId as string) : listChecks(scanId as string),
    enabled: Boolean(scanId) && enabled,
  });
}

/**
 * How this scan compares to the previous one.
 *
 * Like findings, only meaningful once the scan has finished — before then
 * there is no score to compare, and polling for one would fire a request per
 * cycle that is guaranteed to say nothing.
 */
export function useComparison(scanId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: findingKeys.comparisonForScan(scanId ?? ''),
    queryFn: () =>
      USE_FIXTURES ? store.getComparison(scanId as string) : getComparison(scanId as string),
    enabled: Boolean(scanId) && enabled,
  });
}
