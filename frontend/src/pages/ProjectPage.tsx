import { Link, useNavigate, useParams } from 'react-router-dom';
import { Play } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { PageHeader } from '@/components/PageHeader';
import { SectionLabel } from '@/components/SectionLabel';
import { RowList, Row } from '@/components/Rows';
import { Figure } from '@/components/Figure';
import { ProjectSettings } from '@/components/ProjectSettings';
import { ScanStatusBadge } from '@/components/ScanStatusBadge';
import { useProject } from '@/hooks/useProjects';
import { useProjectScans, useStartScan } from '@/hooks/useScan';
import { scoreToGrade } from '@/types/scan';

export default function ProjectPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const navigate = useNavigate();

  const project = useProject(projectId);
  const scans = useProjectScans(projectId);
  const startScan = useStartScan(projectId);

  function handleStartScan() {
    startScan.mutate(undefined, {
      onSuccess: (scan) => navigate(`/scans/${scan.id}`),
    });
  }

  return (
    <div className="space-y-6">
      <PageHeader
        titleSlot={
          project.isPending ? (
            <Skeleton className="h-8 w-48" />
          ) : (
            <h1 className="font-display text-2xl font-semibold tracking-tight">
              {project.data?.name}
            </h1>
          )
        }
        caption={
          <span className="block truncate font-mono text-xs">
            {project.data?.repository_url}
          </span>
        }
        actions={
          <>
            {project.data && <ProjectSettings project={project.data} />}
            <Button onClick={handleStartScan} disabled={startScan.isPending}>
              <Play /> {startScan.isPending ? 'Starting…' : 'Run scan'}
            </Button>
          </>
        }
      />

      {startScan.isError && (
        <Alert variant="destructive">
          <AlertDescription>{startScan.error.message}</AlertDescription>
        </Alert>
      )}

      <section aria-labelledby="scan-history">
        <SectionLabel id="scan-history">Scan history</SectionLabel>

        {scans.isPending && <Skeleton className="h-16 w-full" />}

        {scans.isError && (
          <Alert variant="destructive">
            <AlertDescription>{scans.error.message}</AlertDescription>
          </Alert>
        )}

        {scans.data?.length === 0 && (
          <div className="rounded-lg border border-dashed border-border/60 py-10 text-center text-sm text-muted-foreground">
            No scans yet. Run one to get a readiness score.
          </div>
        )}

        {scans.data && scans.data.length > 0 && (
          <RowList as="ul">
            {scans.data.map((scan) => (
              <Row key={scan.id} as="li" className="py-4">
                <div>
                  <Link
                    to={`/scans/${scan.id}`}
                    className="font-display font-medium tracking-tight hover:underline"
                  >
                    {new Date(scan.created_at).toLocaleString()}
                  </Link>
                  <div className="mt-1">
                    <ScanStatusBadge status={scan.status} />
                  </div>
                </div>

                <div className="text-right">
                  {scan.score === null ? (
                    <>
                      <p className="text-lg font-semibold tabular-nums text-muted-foreground">—</p>
                      <p className="text-xs text-muted-foreground">pending</p>
                    </>
                  ) : (
                    <>
                      <Figure className="block text-lg">{scan.score}</Figure>
                      <p className="font-mono text-xs uppercase tracking-[0.15em] text-muted-foreground">
                        Grade {scoreToGrade(scan.score)}
                      </p>
                    </>
                  )}
                </div>
              </Row>
            ))}
          </RowList>
        )}
      </section>
    </div>
  );
}
