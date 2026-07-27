import { request } from './client';
import type { ScanSummary } from '@/types/scan';

/**
 * Returns immediately with a `pending` scan — the worker picks the job up off
 * Redis afterwards. Never blocks on the scan itself.
 */
export function startScan(projectId: string): Promise<ScanSummary> {
  return request<ScanSummary>(`/projects/${projectId}/scans`, { method: 'POST' });
}

export function listScans(projectId: string): Promise<ScanSummary[]> {
  return request<ScanSummary[]>(`/projects/${projectId}/scans`);
}

/** The endpoint the frontend polls while a scan runs. */
export function getScan(scanId: string): Promise<ScanSummary> {
  return request<ScanSummary>(`/scans/${scanId}`);
}

/** Absolute URL for the PDF report, for use as an href/download target. */
export function reportUrl(scanId: string): string {
  const base = import.meta.env.VITE_API_URL ?? '/api';
  return `${base}/scans/${scanId}/report`;
}
