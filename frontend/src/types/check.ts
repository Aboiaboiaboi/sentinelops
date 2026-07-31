/**
 * What happened to one check.
 *
 * `passed` and `skipped` are the distinction that matters. A scanner that
 * reported findings only could not tell them apart — "this service has a health
 * endpoint" and "this is a CLI tool, the question does not apply" both looked
 * like silence.
 */
export type CheckOutcome = 'passed' | 'failed' | 'skipped';

/** One check a scan performed. Shape of GET /scans/{id}/checks. */
export interface CheckResult {
  id: string;
  category: string;
  title: string;
  outcome: CheckOutcome;
  /** Why it did not apply. Present only for a skipped check. */
  reason: string | null;
}

/** One category's movement between two scans. */
export interface CategoryDelta {
  category: string;
  previous: number | null;
  current: number | null;
  /**
   * Null when either side did not report — which is not zero and not a drop.
   * A category can stop being assessed entirely.
   */
  delta: number | null;
}

/** A check whose outcome moved between two scans. */
export interface CheckChange {
  id: string;
  title: string;
  category: string;
  previous_outcome: CheckOutcome;
  current_outcome: CheckOutcome;
}

/**
 * Shape of GET /scans/{id}/comparison.
 *
 * `previous_scan_id` null means there was nothing to compare against.
 * `comparable` false with a `reason` means there *is* an earlier scan but the
 * difference would mislead — the reason is written to be shown as-is.
 */
export interface ScanComparison {
  previous_scan_id: string | null;
  previous_created_at: string | null;
  previous_score: number | null;
  comparable: boolean;
  reason: string | null;
  score_delta: number | null;
  categories: CategoryDelta[];
  checks: CheckChange[];
}
