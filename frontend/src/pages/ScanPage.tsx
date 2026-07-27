import { Link, useParams } from 'react-router-dom';
import { FileText, TriangleAlert } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { CategoryBreakdownChart } from '@/components/CategoryBreakdownChart';
import { FindingCard } from '@/components/FindingCard';
import { ScanStatusBadge } from '@/components/ScanStatusBadge';
import { ScoreGauge } from '@/components/ScoreGauge';
import { reportedCount, toCategoryScores } from '@/lib/categories';
import { cn } from '@/lib/utils';
import { fixtureCategoryScoresFor } from '@/lib/fixtures';
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
    <Card className={cn(emphasis && 'border-scan-pending/50')}>
      <CardContent className="py-5">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {label}
        </p>
        <p
          className={cn(
            'mt-1 text-2xl font-semibold tabular-nums',
            emphasis && 'text-scan-pending',
          )}
        >
          {value}
        </p>
        {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
      </CardContent>
    </Card>
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

  const categories = toCategoryScores(scan, fixtureCategoryScoresFor(scan.id));
  const { reported, total, complete } = reportedCount(categories);

  return (
    <div className="space-y-6">
      {/* Rendered unconditionally, never behind a status check: a live region has
          to already be in the DOM for a later text change to be announced. */}
      <p className="sr-only" aria-live="polite">
        {scanAnnouncement(scan, reported, total)}
      </p>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Scan results</h1>
          <p className="text-sm text-muted-foreground">
            {new Date(scan.created_at).toLocaleString()}
            {scan.scoring_version && ` · scoring ${scan.scoring_version}`}
          </p>
        </div>

        <div className="flex items-center gap-3">
          <ScanStatusBadge status={scan.status} />
          {finished && (
            <Button variant="outline" asChild>
              <Link to={`/scans/${scan.id}/report`}>
                <FileText /> Report
              </Link>
            </Button>
          )}
        </div>
      </div>

      {scan.status === 'failed' && (
        <Alert variant="destructive">
          <TriangleAlert />
          <AlertDescription>
            This scan failed before it could produce a score.
          </AlertDescription>
        </Alert>
      )}

      {/* Three metric cards, per the frontend spec. The third flags an
          incomplete scan rather than quietly presenting a partial score as whole. */}
      <div className="grid gap-4 sm:grid-cols-3">
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
        <Card className="justify-self-center md:justify-self-start">
          <CardContent className="flex items-center justify-center py-6">
            <ScoreGauge score={scan.score} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Category breakdown</CardTitle>
          </CardHeader>
          <CardContent>
            <CategoryBreakdownChart categories={categories} />
          </CardContent>
        </Card>
      </div>

      <section className="space-y-3">
        <h2 className="text-sm font-medium text-muted-foreground">
          Findings{findings ? ` (${findings.length})` : ''}
        </h2>

        {!finished && (
          <Card>
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              Findings appear once the scan finishes.
            </CardContent>
          </Card>
        )}

        {finished && findingsQuery.isPending && <Skeleton className="h-32 w-full" />}

        {finished && findings?.length === 0 && (
          <Card>
            <CardContent className="py-8 text-center text-sm text-muted-foreground">
              No findings recorded for this scan.
            </CardContent>
          </Card>
        )}

        {findings && findings.length > 0 && (
          <div className="space-y-3">
            {findings.map((finding) => (
              <FindingCard key={finding.id} finding={finding} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
