import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getScan, listScans, renameScan, startScan } from '@/api/scans';
import { USE_FIXTURES, store } from '@/lib/fixtures';
import { isScanFinished, type ScanSummary } from '@/types/scan';
import { projectKeys } from './useProjects';

export const scanKeys = {
  forProject: (projectId: string) => ['projects', projectId, 'scans'] as const,
  detail: (scanId: string) => ['scans', scanId] as const,
};

const POLL_INTERVAL_MS = 3_000;

/**
 * Polls GET /scans/{id} while the scan is in flight and stops on its own once
 * the scan reaches a terminal state. Polling rather than websockets is a
 * deliberate choice.
 */
export function useScan(scanId: string | undefined) {
  return useQuery({
    queryKey: scanKeys.detail(scanId ?? ''),
    queryFn: () => (USE_FIXTURES ? store.getScan(scanId as string) : getScan(scanId as string)),
    enabled: Boolean(scanId),
    // Returning false ends the polling loop — no effect or cleanup needed.
    refetchInterval: (query) =>
      isScanFinished(query.state.data?.status) ? false : POLL_INTERVAL_MS,
    // Keep polling if the user tabs away mid-scan; it's a background job.
    refetchIntervalInBackground: true,
    // A scan in flight is stale the moment it lands.
    staleTime: 0,
  });
}

export function useProjectScans(projectId: string | undefined) {
  return useQuery({
    queryKey: scanKeys.forProject(projectId ?? ''),
    queryFn: () =>
      USE_FIXTURES ? store.listScans(projectId as string) : listScans(projectId as string),
    enabled: Boolean(projectId),
  });
}

export function useStartScan(projectId: string | undefined) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () =>
      USE_FIXTURES ? store.startScan(projectId as string) : startScan(projectId as string),
    onSuccess: (scan: ScanSummary) => {
      // Seed the detail cache so the results page has data on first paint
      // instead of flashing a spinner before the first poll lands.
      client.setQueryData(scanKeys.detail(scan.id), scan);
      if (projectId) {
        client.invalidateQueries({ queryKey: scanKeys.forProject(projectId) });
        client.invalidateQueries({ queryKey: projectKeys.detail(projectId) });
      }
    },
  });
}

/**
 * Names a scan, or clears the name.
 *
 * Writes the result straight into the detail cache rather than invalidating:
 * a rename is a small, known change, and re-fetching a scan the user is
 * looking at would flash a loading state for no new information.
 */
export function useRenameScan(scanId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (name: string | null) =>
      USE_FIXTURES ? store.renameScan(scanId, name) : renameScan(scanId, name),
    onSuccess: (scan: ScanSummary) => {
      client.setQueryData(scanKeys.detail(scanId), scan);
      client.invalidateQueries({ queryKey: scanKeys.forProject(scan.project_id) });
    },
  });
}
