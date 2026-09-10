import { FindingCard } from '@/components/FindingCard';
import { SectionLabel } from '@/components/SectionLabel';
import { categoryLabel } from '@/lib/categories';
import { groupFindingsByCategory } from '@/lib/findings';
import type { Finding } from '@/types/finding';

/**
 * Findings, grouped into one section per category.
 *
 * Shared by the scan page and the report so both read the same way, and so a
 * change to how findings are presented happens once.
 */
export function FindingsList({ findings }: { findings: Finding[] }) {
  const groups = groupFindingsByCategory(findings);

  return (
    <div className="space-y-6">
      {groups.map((group) => (
        <section key={group.category} aria-labelledby={`findings-${group.category}`}>
          <SectionLabel
            as="h3"
            id={`findings-${group.category}`}
            count={
              <span className="normal-case tracking-normal">
                {group.findings.length}{' '}
                {group.findings.length === 1 ? 'finding' : 'findings'}
              </span>
            }
            trailing={
              /* What this category cost, so the section header explains its
                 own share of the score rather than leaving the reader to add
                 up. */
              <span className="font-mono text-xs tabular-nums text-muted-foreground">
                −{group.totalImpact}
              </span>
            }
          >
            {categoryLabel(group.category)}
          </SectionLabel>

          <div className="space-y-3">
            {group.findings.map((finding) => (
              <FindingCard key={finding.id} finding={finding} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
