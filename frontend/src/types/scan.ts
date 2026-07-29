/** Mirrors the Scans.status enum in the backend schema. */
export type ScanStatus = 'pending' | 'running' | 'completed' | 'failed';

/**
 * Per-category outcome within a single scan.
 *
 * `pending` and `failed` are genuinely different states, not cosmetic variants:
 * each scanner category runs in its own sandbox, and one sandbox timing out
 * leaves the scan completable with partial results. The UI has to distinguish
 * "still running" from "gave up" or it misreports what happened.
 */
export type CategoryStatus = 'completed' | 'failed' | 'pending';

/** The six scanner categories the worker runs in parallel. */
export const SCAN_CATEGORIES = [
  'architecture',
  'security',
  'deployment',
  'reliability',
  'observability',
  'scalability',
] as const;

export type ScanCategory = (typeof SCAN_CATEGORIES)[number];

/** Backend sends this as the `category_status` JSON column. */
export type CategoryStatusMap = Partial<Record<string, CategoryStatus>>;

/** One bar in the category breakdown chart. */
export interface CategoryScore {
  category: string;
  status: CategoryStatus;
  /** Points achieved. Null unless status is 'completed'. */
  score: number | null;
  /** This category's weight cap — the denominator for the bar's percentage. */
  maxScore: number;
}

/** Shape of GET /scans/{id} — the endpoint the frontend polls. */
export interface ScanSummary {
  id: string;
  project_id: string;
  status: ScanStatus;
  /** Null until the scan completes. */
  score: number | null;
  /** Which weight-set produced `score`, e.g. "v1". */
  scoring_version: string | null;
  category_status: CategoryStatusMap;
  /**
   * Points each completed category earned. Empty until the scan finishes.
   *
   * Only completed categories appear — a category that did not report is
   * absent rather than zero, since "not assessed" and "assessed and scored
   * nothing" are different things.
   */
  category_scores: Partial<Record<string, number>>;
  /** Each category's cap. Sent by the API so this app needs no copy of the
   * weight table. */
  category_max_scores: Partial<Record<string, number>>;
  created_at: string;
}

/** A scan's terminal states — polling stops here. */
export function isScanFinished(status: ScanStatus | undefined): boolean {
  return status === 'completed' || status === 'failed';
}

/** 0–100 numeric score to a letter grade. */
export function scoreToGrade(score: number | null): string {
  if (score === null) return '—';
  if (score >= 90) return 'A';
  if (score >= 80) return 'B';
  if (score >= 70) return 'C';
  if (score >= 60) return 'D';
  return 'F';
}

/**
 * Spoken text for the scan page's live region.
 *
 * That page mutates the status badge, score and chart in place every 3s as it
 * polls. Sighted users see it happen; without an announcement a screen reader
 * user is never told the scan finished, and would have to re-read the page to
 * find out.
 */
export function scanAnnouncement(
  scan: Pick<ScanSummary, 'status' | 'score'>,
  reported: number,
  total: number,
): string {
  switch (scan.status) {
    case 'pending':
      return 'Scan queued.';
    case 'running':
      return `Scan in progress. ${reported} of ${total} categories reported.`;
    case 'failed':
      return 'Scan failed before it could produce a score.';
    case 'completed':
      return scan.score === null
        ? 'Scan completed without a score.'
        : `Scan completed. Score ${scan.score} out of 100, grade ${scoreToGrade(scan.score)}. ${reported} of ${total} categories reported.`;
  }
}
