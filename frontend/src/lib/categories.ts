import type { CategoryScore, CategoryStatus, ScanSummary } from '@/types/scan';

/**
 * Display names for the six scanner categories.
 *
 * Labels only. The weights used to live here too, as a hand-synced copy of the
 * backend's rubric — the API now returns `category_max_scores`, so there is one
 * source of truth and no table to keep in step.
 */
const CATEGORY_LABELS: Record<string, string> = {
  security: 'Security',
  reliability: 'Reliability',
  architecture: 'Architecture',
  deployment: 'Deployment',
  observability: 'Observability',
  scalability: 'Scalability',
};

function titleCase(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

export function categoryLabel(category: string): string {
  return CATEGORY_LABELS[category] ?? titleCase(category);
}

/**
 * Turns a scan into chart rows.
 *
 * Both the points and the cap come from the scan itself. Previously a completed
 * category with no points supplied was drawn at its full cap, which made the
 * chart contradict the headline score — a category worth 20 that lost 3 still
 * showed a full bar.
 *
 * A completed category missing from `category_scores` still falls back to its
 * cap, which is what old scans recorded before the API sent points.
 */
export function toCategoryScores(
  scan: Pick<ScanSummary, 'category_status' | 'category_scores' | 'category_max_scores'>,
): CategoryScore[] {
  const entries = Object.entries(scan.category_status) as [string, CategoryStatus][];

  return entries
    .filter(([, status]) => Boolean(status))
    .map(([category, status]) => {
      const maxScore = scan.category_max_scores[category] ?? 0;
      return {
        category,
        status,
        score: status === 'completed' ? (scan.category_scores[category] ?? maxScore) : null,
        maxScore,
      };
    })
    .sort((a, b) => b.maxScore - a.maxScore || a.category.localeCompare(b.category));
}

/** "4 of 6" for the metric card. Counts only categories that actually reported. */
export function reportedCount(categories: CategoryScore[]): {
  reported: number;
  total: number;
  complete: boolean;
} {
  const reported = categories.filter((c) => c.status === 'completed').length;
  return { reported, total: categories.length, complete: reported === categories.length };
}
