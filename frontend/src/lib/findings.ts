import type { Finding } from '@/types/finding';

/** Findings for one category, with what they cost between them. */
export interface FindingGroup {
  category: string;
  findings: Finding[];
  /** Points this category lost in total — the section's headline number. */
  totalImpact: number;
}

/**
 * Group findings by category, worst-hit category first.
 *
 * A flat list makes the reader do the grouping in their head: two deployment
 * problems and one security problem read as three unrelated items, when they
 * are really two jobs. Ordering by points lost rather than alphabetically puts
 * the category most worth fixing at the top.
 *
 * Order within a group is preserved, so the API's most-severe-first sort
 * survives grouping.
 */
export function groupFindingsByCategory(findings: Finding[]): FindingGroup[] {
  const groups = new Map<string, Finding[]>();

  for (const finding of findings) {
    const existing = groups.get(finding.category);
    if (existing) {
      existing.push(finding);
    } else {
      groups.set(finding.category, [finding]);
    }
  }

  return [...groups.entries()]
    .map(([category, categoryFindings]) => ({
      category,
      findings: categoryFindings,
      totalImpact: categoryFindings.reduce((total, f) => total + f.score_impact, 0),
    }))
    .sort((a, b) => b.totalImpact - a.totalImpact || a.category.localeCompare(b.category));
}
