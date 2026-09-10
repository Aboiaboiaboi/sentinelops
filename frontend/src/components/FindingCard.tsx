import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import type { Finding, Severity } from '@/types/finding';

const SEVERITY_CLASS: Record<Severity, string> = {
  CRITICAL: 'bg-severity-critical/15 text-severity-critical border-severity-critical/40',
  HIGH: 'bg-severity-high/15 text-severity-high border-severity-high/40',
  MEDIUM: 'bg-severity-medium/15 text-severity-medium border-severity-medium/40',
  LOW: 'bg-severity-low/15 text-severity-low border-severity-low/40',
};

export function FindingCard({ finding }: { finding: Finding }) {
  return (
    <Card className="border-border/60 bg-card/60">
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <CardTitle className="font-display text-base tracking-tight">{finding.title}</CardTitle>
          <div className="flex shrink-0 items-center gap-2">
            <Badge
              variant="outline"
              className={cn(
                'font-mono text-[0.625rem] uppercase tracking-[0.12em]',
                SEVERITY_CLASS[finding.severity],
              )}
            >
              {finding.severity}
            </Badge>
            {/* Score impact is what makes a score explainable rather than opaque. */}
            <Badge variant="secondary" className="font-mono tabular-nums">
              −{finding.score_impact}
            </Badge>
          </div>
        </div>
        {/* No category label here: FindingsList groups by category, so the
            section heading already says it and repeating it on every card was
            noise. */}
      </CardHeader>

      <CardContent className="space-y-3 text-sm">
        <p className="text-muted-foreground">{finding.description}</p>
        <div className="border-l-2 border-primary-bright/40 pl-4">
          <p className="mb-1 font-mono text-xs font-medium uppercase tracking-[0.15em] text-muted-foreground">
            Recommendation
          </p>
          <p>{finding.recommendation}</p>
        </div>
      </CardContent>
    </Card>
  );
}
