import { request } from './client';
import type { Finding } from '@/types/finding';

export function listFindings(scanId: string): Promise<Finding[]> {
  return request<Finding[]>(`/scans/${scanId}/findings`);
}
