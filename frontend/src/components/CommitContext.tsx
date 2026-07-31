import { GitCommitHorizontal } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import type { ScanSummary } from '@/types/scan';

/** Short form, as every git tool shows it. */
function shortSha(sha: string): string {
  return sha.slice(0, 7);
}

function formatCommittedAt(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString();
}

/**
 * What the scan actually looked at, shown above the findings.
 *
 * Renders nothing without a commit — an empty repository has none, and scans
 * recorded before this was captured have none either. An absent commit is not
 * worth a placeholder saying so.
 */
export function CommitContext({ scan }: { scan: ScanSummary }) {
  if (!scan.commit_sha) return null;

  return (
    <Card>
      <CardContent className="flex items-start gap-3 py-4">
        <GitCommitHorizontal
          className="mt-0.5 size-4 shrink-0 text-muted-foreground"
          aria-hidden="true"
        />
        <div className="min-w-0 space-y-1">
          {/* Empty messages are legal in git, so the subject may be blank —
              the metadata line below still identifies the commit. */}
          {scan.commit_message && (
            <p className="text-sm font-medium break-words">{scan.commit_message}</p>
          )}
          <p className="text-xs text-muted-foreground">
            <code className="font-mono">{shortSha(scan.commit_sha)}</code>
            {scan.commit_author && <> · {scan.commit_author}</>}
            {scan.committed_at && <> · {formatCommittedAt(scan.committed_at)}</>}
          </p>
        </div>
      </CardContent>
    </Card>
  );
}
