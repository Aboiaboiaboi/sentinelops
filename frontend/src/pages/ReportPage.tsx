import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, Download } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { CategoryBreakdownChart } from '@/components/CategoryBreakdownChart';
import { CommitContext } from '@/components/CommitContext';
import { FindingsList } from '@/components/FindingsList';
import { ScoreGauge } from '@/components/ScoreGauge';
import { reportUrl } from '@/api/scans';
import { reportedCount, toCategoryScores } from '@/lib/categories';
import { useFindings } from '@/hooks/useFindings';
import { useScan } from '@/hooks/useScan';
import { isScanFinished, scoreToGrade } from '@/types/scan';

/**
 * Read-only, print-friendly view of a completed scan. The PDF itself is
 * generated server-side (GET /scans/{id}/report) — this is the on-screen
 * equivalent.
 */
export default function ReportPage() {
  const { scanId } = useParams<{ scanId: string }>();
  const query = useScan(scanId);

  const scan = query.data;
  const finished = isScanFinished(scan?.status);

  const findingsQuery = useFindings(scanId, finished);
  const findings = findingsQuery.data;

  if (query.isPending) {
    return <Skeleton className="h-96 w-full" />;
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
      <div className="flex flex-wrap items-center justify-between gap-3 print:hidden">
        <Button variant="ghost" asChild>
          <Link to={`/scans/${scan.id}`}>
            <ArrowLeft /> Back to scan
          </Link>
        </Button>

        <Button variant="outline" asChild>
          {/* Server-rendered PDF; opens in a new tab rather than replacing the app. */}
          <a href={reportUrl(scan.id)} target="_blank" rel="noreferrer">
            <Download /> Download PDF
          </a>
        </Button>
      </div>

      <header className="space-y-1">
        <h1 className="text-2xl font-semibold">Production readiness report</h1>
        <p className="text-sm text-muted-foreground">
          Generated {new Date(scan.created_at).toLocaleString()}
          {scan.scoring_version && ` · scoring ${scan.scoring_version}`}
        </p>
      </header>

      {!complete && (
        <Alert variant="warning">
          <AlertDescription>
            {reported} of {total} categories reported. The score reflects only those
            categories.
          </AlertDescription>
        </Alert>
      )}

      <Card>
        <CardContent className="flex flex-wrap items-center gap-8 py-6">
          <ScoreGauge score={scan.score} />
          <dl className="grid gap-3 text-sm">
            <div>
              <dt className="text-muted-foreground">Overall</dt>
              <dd className="text-lg font-semibold tabular-nums">
                {scan.score === null ? '—' : `${scan.score}/100`}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Grade</dt>
              <dd className="text-lg font-semibold">{scoreToGrade(scan.score)}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Categories reported</dt>
              <dd className="text-lg font-semibold tabular-nums">
                {reported}/{total}
              </dd>
            </div>
          </dl>
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

      <CommitContext scan={scan} />

      <section className="space-y-3">
        <h2 className="text-sm font-medium text-muted-foreground">
          Findings{findings ? ` (${findings.length})` : ''}
        </h2>
        {findings?.length === 0 && (
          <p className="text-sm text-muted-foreground">No findings recorded.</p>
        )}
        {findings && findings.length > 0 && <FindingsList findings={findings} />}
      </section>
    </div>
  );
}
