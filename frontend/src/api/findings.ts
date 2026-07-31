import { request } from './client';
import type { Finding } from '@/types/finding';
import type { CheckResult } from '@/types/check';

export function listFindings(scanId: string): Promise<Finding[]> {
  return request<Finding[]>(`/scans/${scanId}/findings`);
}

/**
 * Every check the scan performed, with its outcome.
 *
 * Its own endpoint rather than a field on the scan: the scan is polled every
 * three seconds and stays one cheap row read, while this is fetched once when
 * somebody expands the detail.
 */
export function listChecks(scanId: string): Promise<CheckResult[]> {
  return request<CheckResult[]>(`/scans/${scanId}/checks`);
}
