import { describe, expect, it } from 'vitest';
import { groupFindingsByCategory } from '@/lib/findings';
import type { Finding, Severity } from '@/types/finding';

function finding(
  id: string,
  category: string,
  score_impact: number,
  severity: Severity = 'HIGH',
): Finding {
  return {
    id,
    scan_id: 'scan-1',
    category,
    severity,
    title: `finding ${id}`,
    description: '',
    recommendation: '',
    score_impact,
  };
}

describe('groupFindingsByCategory', () => {
  it('puts every finding for a category in one group', () => {
    const groups = groupFindingsByCategory([
      finding('a', 'deployment', 4),
      // 8 rather than 6: at 6 this ties deployment's 4 + 2 and the assertion
      // below would be testing the alphabetical tiebreak instead of grouping.
      finding('b', 'security', 8),
      finding('c', 'deployment', 2),
    ]);

    expect(groups.map((g) => g.category)).toEqual(['security', 'deployment']);
    expect(groups[1].findings.map((f) => f.id)).toEqual(['a', 'c']);
  });

  it('totals what each category cost', () => {
    const groups = groupFindingsByCategory([
      finding('a', 'deployment', 4),
      finding('b', 'deployment', 2),
    ]);

    expect(groups[0].totalImpact).toBe(6);
  });

  it('orders by points lost, so the category most worth fixing is first', () => {
    const groups = groupFindingsByCategory([
      finding('a', 'observability', 4),
      finding('b', 'security', 6),
      finding('c', 'deployment', 5),
    ]);

    expect(groups.map((g) => g.category)).toEqual(['security', 'deployment', 'observability']);
  });

  it('breaks ties by name so the order never jitters between renders', () => {
    const groups = groupFindingsByCategory([
      finding('a', 'security', 4),
      finding('b', 'architecture', 4),
    ]);

    expect(groups.map((g) => g.category)).toEqual(['architecture', 'security']);
  });

  it('preserves order within a group, keeping the API most-severe-first sort', () => {
    const groups = groupFindingsByCategory([
      finding('a', 'security', 6, 'CRITICAL'),
      finding('b', 'security', 4, 'HIGH'),
      finding('c', 'security', 2, 'LOW'),
    ]);

    expect(groups[0].findings.map((f) => f.severity)).toEqual(['CRITICAL', 'HIGH', 'LOW']);
  });

  it('returns nothing for no findings', () => {
    expect(groupFindingsByCategory([])).toEqual([]);
  });
});
