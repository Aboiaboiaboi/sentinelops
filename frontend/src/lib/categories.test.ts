import { describe, expect, it } from 'vitest';
import { categoryLabel, reportedCount, toCategoryScores } from './categories';
import type { CategoryStatusMap, ScanSummary } from '@/types/scan';

/** The rubric the API sends alongside every scan. */
const MAX_SCORES = {
  security: 25,
  reliability: 20,
  architecture: 20,
  deployment: 15,
  observability: 10,
  scalability: 10,
};

type ChartInput = Pick<
  ScanSummary,
  'category_status' | 'category_scores' | 'category_max_scores'
>;

function scan(
  category_status: CategoryStatusMap,
  category_scores: Partial<Record<string, number>> = {},
): ChartInput {
  return { category_status, category_scores, category_max_scores: MAX_SCORES };
}

const allStates: CategoryStatusMap = {
  security: 'completed',
  observability: 'pending',
  deployment: 'failed',
};

describe('categoryLabel', () => {
  it('uses the configured label for known categories', () => {
    expect(categoryLabel('security')).toBe('Security');
  });

  it('title-cases anything the backend adds later', () => {
    expect(categoryLabel('maintainability')).toBe('Maintainability');
  });
});

describe('toCategoryScores', () => {
  it('only gives completed categories a score', () => {
    const rows = toCategoryScores(scan(allStates, { security: 25 }));
    const byCategory = Object.fromEntries(rows.map((r) => [r.category, r]));

    // A non-completed category has no measurement — null, not 0. Zero would
    // render as a real "scored nothing" result.
    expect(byCategory.security.score).toBe(25);
    expect(byCategory.observability.score).toBeNull();
    expect(byCategory.deployment.score).toBeNull();
  });

  it('shows the points a category actually earned', () => {
    // The bug this replaced: a category worth 20 that lost 3 still drew a full
    // bar, so the chart contradicted the headline score.
    const rows = toCategoryScores(
      scan({ architecture: 'completed' }, { architecture: 17 }),
    );

    expect(rows[0]).toMatchObject({ score: 17, maxScore: 20 });
  });

  it('takes the cap from the API rather than a local table', () => {
    const rows = toCategoryScores({
      category_status: { security: 'completed' },
      category_scores: { security: 3 },
      category_max_scores: { security: 5 },
    });

    expect(rows[0]).toMatchObject({ score: 3, maxScore: 5 });
  });

  it('falls back to the cap when a completed category has no points', () => {
    // Scans recorded before the API sent per-category points.
    const rows = toCategoryScores(scan({ security: 'completed' }));

    expect(rows[0]).toMatchObject({ score: 25, maxScore: 25 });
  });

  it('preserves each category status verbatim', () => {
    const rows = toCategoryScores(scan(allStates));
    expect(rows.map((r) => r.status).sort()).toEqual(['completed', 'failed', 'pending']);
  });

  it('sorts by weight descending, then category name', () => {
    const rows = toCategoryScores(
      scan({ scalability: 'completed', security: 'completed', observability: 'completed' }),
    );
    // scalability and observability both weigh 10, so they tie-break by name.
    expect(rows.map((r) => r.category)).toEqual(['security', 'observability', 'scalability']);
  });

  it('drops categories the backend omitted', () => {
    const rows = toCategoryScores(scan({ security: 'completed', deployment: undefined }));
    expect(rows.map((r) => r.category)).toEqual(['security']);
  });

  it('returns no rows for an empty status map', () => {
    expect(toCategoryScores(scan({}))).toEqual([]);
  });

  it('gives an unrecognised category a zero cap rather than NaN', () => {
    const rows = toCategoryScores(scan({ cicd: 'completed' }));
    expect(rows[0].maxScore).toBe(0);
  });
});

describe('reportedCount', () => {
  it('counts only categories that actually reported', () => {
    const rows = toCategoryScores(scan(allStates));
    expect(reportedCount(rows)).toEqual({ reported: 1, total: 3, complete: false });
  });

  // The "score covers reported categories only" warning hangs off `complete`,
  // so it must not appear when every category did report.
  it('is complete when nothing is pending or failed', () => {
    const rows = toCategoryScores(scan({ security: 'completed', reliability: 'completed' }));
    expect(reportedCount(rows)).toEqual({ reported: 2, total: 2, complete: true });
  });
});
