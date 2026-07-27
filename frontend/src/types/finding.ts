/** Mirrors the Findings.severity enum in the backend schema. */
export type Severity = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';

/** Most-severe-first, for sorting findings in the UI. */
export const SEVERITY_ORDER: Record<Severity, number> = {
  CRITICAL: 0,
  HIGH: 1,
  MEDIUM: 2,
  LOW: 3,
};

/** One row of the Findings table — what a scanner emits, normalised. */
export interface Finding {
  id: string;
  scan_id: string;
  category: string;
  severity: Severity;
  title: string;
  description: string;
  recommendation: string;
  /** Points this specific finding deducted. Drives score explainability. */
  score_impact: number;
}
