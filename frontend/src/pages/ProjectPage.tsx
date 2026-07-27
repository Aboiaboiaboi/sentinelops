import { Link, useNavigate, useParams } from 'react-router-dom';
import { Play } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Alert, AlertDescription } from '@/components/ui/alert';
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
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          {project.isPending ? (
            <Skeleton className="h-8 w-48" />
          ) : (
            <h1 className="text-2xl font-semibold">{project.data?.name}</h1>
          )}
          <p className="truncate text-sm text-muted-foreground">
            {project.data?.repository_url}
          </p>
        </div>

        <Button onClick={handleStartScan} disabled={startScan.isPending}>
          <Play /> {startScan.isPending ? 'Starting…' : 'Run scan'}
        </Button>
      </div>

      {startScan.isError && (
        <Alert variant="destructive">
          <AlertDescription>{startScan.error.message}</AlertDescription>
        </Alert>
      )}

      <section className="space-y-3">
        <h2 className="text-sm font-medium text-muted-foreground">Scan history</h2>

        {scans.isPending && <Skeleton className="h-16 w-full" />}

        {scans.isError && (
          <Alert variant="destructive">
            <AlertDescription>{scans.error.message}</AlertDescription>
          </Alert>
        )}

        {scans.data?.length === 0 && (
          <Card>
            <CardContent className="py-10 text-center text-sm text-muted-foreground">
              No scans yet. Run one to get a readiness score.
            </CardContent>
          </Card>
        )}

        {scans.data && scans.data.length > 0 && (
          <ul className="space-y-2">
            {scans.data.map((scan) => (
              <li key={scan.id}>
                <Card>
                  <CardContent className="flex items-center justify-between gap-4 py-4">
                    <div>
                      <Link to={`/scans/${scan.id}`} className="font-medium hover:underline">
                        {new Date(scan.created_at).toLocaleString()}
                      </Link>
                      <div className="mt-1">
                        <ScanStatusBadge status={scan.status} />
                      </div>
                    </div>

                    <div className="text-right tabular-nums">
                      <p className="text-lg font-semibold">{scan.score ?? '—'}</p>
                      <p className="text-xs text-muted-foreground">
                        {scan.score === null ? 'pending' : `Grade ${scoreToGrade(scan.score)}`}
                      </p>
                    </div>
                  </CardContent>
                </Card>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
