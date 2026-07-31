import { request } from './client';
import type { Finding } from '@/types/finding';
import type { CheckResult, ScanComparison } from '@/types/check';

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

/**
 * How this scan differs from the previous completed scan of the same project.
 *
 * Always 200 — no earlier scan is a normal state, not an error, so the client
 * renders nothing rather than handling a failure.
 */
export function getComparison(scanId: string): Promise<ScanComparison> {
  return request<ScanComparison>(`/scans/${scanId}/comparison`);
}
