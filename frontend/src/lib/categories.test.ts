import { describe, expect, it } from 'vitest';
import { categoryLabel, categoryWeight, reportedCount, toCategoryScores } from './categories';
import type { CategoryStatusMap } from '@/types/scan';

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

describe('categoryWeight', () => {
  it('sums to 100 across the six scanner categories', () => {
    const total = ['security', 'reliability', 'architecture', 'deployment', 'observability', 'scalability']
      .map(categoryWeight)
      .reduce((a, b) => a + b, 0);
    expect(total).toBe(100);
  });

  it('returns 0 for a category with no weight, rather than NaN', () => {
    expect(categoryWeight('cicd')).toBe(0);
  });
});

describe('toCategoryScores', () => {
  it('only gives completed categories a score', () => {
    const rows = toCategoryScores({ category_status: allStates });
    const byCategory = Object.fromEntries(rows.map((r) => [r.category, r]));

    // A non-completed category has no measurement — null, not 0. Zero would
    // render as a real "scored nothing" result.
    expect(byCategory.security.score).toBe(25);
    expect(byCategory.observability.score).toBeNull();
    expect(byCategory.deployment.score).toBeNull();
  });

  it('preserves each category status verbatim', () => {
    const rows = toCategoryScores({ category_status: allStates });
    expect(rows.map((r) => r.status).sort()).toEqual(['completed', 'failed', 'pending']);
  });

  it('prefers explicit per-category points over the weight cap', () => {
    const rows = toCategoryScores({ category_status: { security: 'completed' } }, { security: 14 });
    expect(rows[0]).toMatchObject({ score: 14, maxScore: 25 });
  });

  it('sorts by weight descending, then category name', () => {
    const rows = toCategoryScores({
      category_status: {
        scalability: 'completed',
        security: 'completed',
        observability: 'completed',
      },
    });
    // scalability and observability both weigh 10, so they tie-break by name.
    expect(rows.map((r) => r.category)).toEqual(['security', 'observability', 'scalability']);
  });

  it('drops categories the backend omitted', () => {
    const rows = toCategoryScores({
      category_status: { security: 'completed', deployment: undefined },
    });
    expect(rows.map((r) => r.category)).toEqual(['security']);
  });

  it('returns no rows for an empty status map', () => {
    expect(toCategoryScores({ category_status: {} })).toEqual([]);
  });
});

describe('reportedCount', () => {
  it('counts only categories that actually reported', () => {
    const rows = toCategoryScores({ category_status: allStates });
    expect(reportedCount(rows)).toEqual({ reported: 1, total: 3, complete: false });
  });

  // The "score covers reported categories only" warning hangs off `complete`,
  // so it must not appear when every category did report.
  it('is complete when nothing is pending or failed', () => {
    const rows = toCategoryScores({
      category_status: { security: 'completed', reliability: 'completed' },
    });
    expect(reportedCount(rows)).toEqual({ reported: 2, total: 2, complete: true });
  });
});
