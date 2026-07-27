import type { CategoryScore, CategoryStatus, ScanSummary } from '@/types/scan';

/**
 * Display labels and weight caps for the six scanner categories.
 *
 * ⚠️ PLACEHOLDER — needs a backend decision.
 * The scanner categories (the keys of `category_status`) and the scoring
 * weights are not yet a single agreed list, so the weights below are a
 * placeholder mapping onto the six real scanner categories, summing to 100.
 * They exist so the chart renders sensibly today.
 *
 * The backend should ultimately return each category's `maxScore` in the API
 * response so this table can be deleted rather than kept in sync by hand.
 */
export const CATEGORY_META: Record<string, { label: string; weight: number }> = {
  security: { label: 'Security', weight: 25 },
  reliability: { label: 'Reliability', weight: 20 },
  architecture: { label: 'Architecture', weight: 20 },
  deployment: { label: 'Deployment', weight: 15 },
  observability: { label: 'Observability', weight: 10 },
  scalability: { label: 'Scalability', weight: 10 },
};

function titleCase(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

export function categoryLabel(category: string): string {
  return CATEGORY_META[category]?.label ?? titleCase(category);
}

export function categoryWeight(category: string): number {
  return CATEGORY_META[category]?.weight ?? 0;
}

/**
 * Turns a scan's `category_status` map into chart rows.
 *
 * `scores` is optional because the API does not yet return per-category
 * points — only the overall score and each category's status. Until it does,
 * completed categories render at their full weight cap.
 */
export function toCategoryScores(
  scan: Pick<ScanSummary, 'category_status'>,
  scores?: Partial<Record<string, number>>,
): CategoryScore[] {
  const entries = Object.entries(scan.category_status) as [string, CategoryStatus][];

  return entries
    .filter(([, status]) => Boolean(status))
    .map(([category, status]) => {
      const maxScore = categoryWeight(category);
      return {
        category,
        status,
        score: status === 'completed' ? (scores?.[category] ?? maxScore) : null,
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
