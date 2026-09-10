import { Link, useParams } from 'react-router-dom';
import { ArrowLeft, Download } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { PageHeader } from '@/components/PageHeader';
import { SectionLabel } from '@/components/SectionLabel';
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

        {/*
          A link only once there is something to download. The endpoint answers
          409 for a scan that has not finished, and an anchor cannot be
          disabled — `disabled` on an <a> is ignored, so `asChild` here would
          render a control that looks unavailable and still navigates. A real
          <button> is the only element the attribute means anything on.
        */}
        {finished ? (
          <Button variant="outline" asChild>
            {/* Server-rendered PDF; opens in a new tab rather than replacing the app. */}
            <a href={reportUrl(scan.id)} target="_blank" rel="noreferrer">
              <Download /> Download PDF
            </a>
          </Button>
        ) : (
          <Button variant="outline" disabled title="Available once the scan finishes">
            <Download /> Download PDF
          </Button>
        )}
      </div>

      <PageHeader
        eyebrow="Report"
        caption={
          <>
            Generated {new Date(scan.created_at).toLocaleString()}
            {scan.scoring_version && ` · scoring ${scan.scoring_version}`}
          </>
        }
      >
        Production readiness report
      </PageHeader>

      {!complete && (
        <Alert variant="warning">
          <AlertDescription>
            {reported} of {total} categories reported. The score reflects only those
            categories.
          </AlertDescription>
        </Alert>
      )}

      <Card className="border-border/60 bg-card/60">
        <CardContent className="flex flex-wrap items-center gap-8 py-6">
          <ScoreGauge score={scan.score} />
          <dl className="grid gap-4 text-sm sm:grid-cols-3">
            <div className="space-y-1">
              <dt className="font-mono text-xs font-medium uppercase tracking-[0.15em] text-muted-foreground">
                Overall
              </dt>
              <dd className="font-display text-xl font-semibold tracking-tight tabular-nums">
                {scan.score === null ? '—' : `${scan.score}/100`}
              </dd>
            </div>
            <div className="space-y-1">
              <dt className="font-mono text-xs font-medium uppercase tracking-[0.15em] text-muted-foreground">
                Grade
              </dt>
              <dd className="font-display text-xl font-semibold tracking-tight">
                {scoreToGrade(scan.score)}
              </dd>
            </div>
            <div className="space-y-1">
              <dt className="font-mono text-xs font-medium uppercase tracking-[0.15em] text-muted-foreground">
                Categories reported
              </dt>
              <dd className="font-display text-xl font-semibold tracking-tight tabular-nums">
                {reported}/{total}
              </dd>
            </div>
          </dl>
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

      <CommitContext scan={scan} />

      <section aria-labelledby="report-findings">
        <SectionLabel id="report-findings" count={findings ? findings.length : undefined}>
          Findings
        </SectionLabel>
        {findings?.length === 0 && (
          <p className="text-sm text-muted-foreground">No findings recorded.</p>
        )}
        {findings && findings.length > 0 && <FindingsList findings={findings} />}
      </section>
    </div>
  );
}
