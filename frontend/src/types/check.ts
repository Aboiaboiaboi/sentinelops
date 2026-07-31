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
