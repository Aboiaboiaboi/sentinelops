import { Link, useParams } from 'react-router-dom';
import { FileText } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { PageHeader } from '@/components/PageHeader';
import { SectionLabel } from '@/components/SectionLabel';
import { Figure } from '@/components/Figure';
import { CategoryBreakdownChart } from '@/components/CategoryBreakdownChart';
import { CommitContext } from '@/components/CommitContext';
import { ScanChecks } from '@/components/ScanChecks';
import { ScanComparison } from '@/components/ScanComparison';
import { ScanFailure } from '@/components/ScanFailure';
import { ScanName } from '@/components/ScanName';
import { FindingsList } from '@/components/FindingsList';
import { ScanStatusBadge } from '@/components/ScanStatusBadge';
import { ScoreGauge } from '@/components/ScoreGauge';
import { reportedCount, toCategoryScores } from '@/lib/categories';
import { cn } from '@/lib/utils';
import { useFindings } from '@/hooks/useFindings';
import { useScan } from '@/hooks/useScan';
import { isScanFinished, scanAnnouncement, scoreToGrade } from '@/types/scan';

function MetricCard({
  label,
  value,
  hint,
  emphasis,
}: {
  label: string;
  value: string;
  hint?: string;
  emphasis?: boolean;
}) {
  return (
    <div className={cn('bg-card/40 px-4 py-5', emphasis && 'bg-scan-pending/10')}>
      <p className="font-mono text-xs font-medium uppercase tracking-[0.15em] text-muted-foreground">
        {label}
      </p>
      {/* Gold for a plain figure; the pending-emphasis value keeps its own
          amber so "score covers reported categories only" still reads as a
          warning, not decoration. */}
      {emphasis ? (
        <p className="mt-1 text-2xl font-semibold tabular-nums text-scan-pending">{value}</p>
      ) : (
        <Figure className="mt-1 block text-2xl">{value}</Figure>
      )}
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

export default function ScanPage() {
  const { scanId } = useParams<{ scanId: string }>();
  const query = useScan(scanId);

  const scan = query.data;
  const finished = isScanFinished(scan?.status);

  const findingsQuery = useFindings(scanId, finished);
  const findings = findingsQuery.data;

  if (query.isPending) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-40" />
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (query.isError) {
    return (
      <Alert variant="destructive">
        <AlertDescription>{query.error.message}</AlertDescription>
      </Alert>
    );
  }

  if (!scan) return null;

  const categories = toCategoryScores(scan);
  const { reported, total, complete } = reportedCount(categories);

  return (
    <div className="space-y-6">
      {/* Rendered unconditionally, never behind a status check: a live region has
          to already be in the DOM for a later text change to be announced. */}
      <p className="sr-only" aria-live="polite">
        {scanAnnouncement(scan, reported, total)}
      </p>

      <PageHeader
        titleSlot={<ScanName scan={scan} />}
        caption={
          <>
            {new Date(scan.created_at).toLocaleString()}
            {scan.scoring_version && ` · scoring ${scan.scoring_version}`}
          </>
        }
        actions={
          <>
            <ScanStatusBadge status={scan.status} />
            {finished && (
              <Button variant="outline" asChild>
                <Link to={`/scans/${scan.id}/report`}>
                  <FileText /> Report
                </Link>
              </Button>
            )}
          </>
        }
      />

      <ScanFailure scan={scan} />

      {/* Three metrics as one divided strip. The third flags an incomplete
          scan rather than quietly presenting a partial score as whole. */}
      <div className="grid overflow-hidden rounded-lg border border-border/60 sm:grid-cols-3 sm:divide-x sm:divide-border/60 [&>*+*]:border-t [&>*+*]:border-border/60 sm:[&>*+*]:border-t-0">
        <MetricCard
          label="Overall score"
          value={scan.score === null ? '—' : `${scan.score}/100`}
          hint={scan.score === null ? 'Not yet scored' : undefined}
        />
        <MetricCard label="Grade" value={scoreToGrade(scan.score)} />
        <MetricCard
          label="Categories reported"
          value={`${reported}/${total}`}
          hint={complete ? 'All categories reported' : 'Score covers reported categories only'}
          emphasis={!complete}
        />
      </div>

      <div className="grid gap-6 md:grid-cols-[auto_1fr] md:items-start">
        <Card className="justify-self-center border-border/60 bg-card/60 md:justify-self-start">
          <CardContent className="flex items-center justify-center py-6">
            <ScoreGauge score={scan.score} />
          </CardContent>
        </Card>

        <Card className="border-border/60 bg-card/60">
          <CardHeader className="pb-2">
            <CardTitle className="font-display text-base tracking-tight">
              Category breakdown
            </CardTitle>
          </CardHeader>
          <CardContent>
            <CategoryBreakdownChart categories={categories} />
          </CardContent>
        </Card>
      </div>

      <CommitContext scan={scan} />

      <ScanComparison scanId={scan.id} enabled={scan.status === 'completed'} />

      {finished && <ScanChecks scanId={scan.id} />}

      <section aria-labelledby="scan-findings">
        <SectionLabel id="scan-findings" count={findings ? findings.length : undefined}>
          Findings
        </SectionLabel>

        {!finished && (
          <div className="rounded-lg border border-dashed border-border/60 py-8 text-center text-sm text-muted-foreground">
            Findings appear once the scan finishes.
          </div>
        )}

        {finished && findingsQuery.isPending && <Skeleton className="h-32 w-full" />}

        {finished && findings?.length === 0 && (
          <div className="rounded-lg border border-dashed border-border/60 py-8 text-center text-sm text-muted-foreground">
            No findings recorded for this scan.
          </div>
        )}

        {findings && findings.length > 0 && <FindingsList findings={findings} />}
      </section>
    </div>
  );
}
