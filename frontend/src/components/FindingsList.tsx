import { FindingCard } from '@/components/FindingCard';
import { Badge } from '@/components/ui/badge';
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
          <div className="mb-2 flex items-baseline justify-between gap-2 border-b pb-1.5">
            <h3 id={`findings-${group.category}`} className="text-sm font-medium">
              {categoryLabel(group.category)}
              <span className="ml-2 text-xs font-normal text-muted-foreground">
                {group.findings.length}{' '}
                {group.findings.length === 1 ? 'finding' : 'findings'}
              </span>
            </h3>
            {/* What this category cost, so the section header explains its own
                share of the score rather than leaving the reader to add up. */}
            <Badge variant="secondary" className="shrink-0 tabular-nums">
              −{group.totalImpact}
            </Badge>
          </div>

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
