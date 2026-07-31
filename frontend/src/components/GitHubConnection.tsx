import { Github } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { installUrl } from '@/api/github';
import { useGitHubInstallations } from '@/hooks/useGitHub';

/**
 * Connect-to-GitHub state, for scanning private repositories.
 *
 * Hidden entirely when the backend has no App configured — that is a 503, and
 * offering a button that cannot work would be worse than saying nothing. Public
 * repositories need none of this.
 */
export function GitHubConnection() {
  const { data: installations, isPending, isError } = useGitHubInstallations();

  if (isPending) return <Skeleton className="h-16 w-full" />;
  if (isError) return null;

  const connected = installations && installations.length > 0;

  return (
    <Card>
      <CardContent className="flex flex-wrap items-center justify-between gap-3 py-4">
        <div className="flex min-w-0 items-center gap-3">
          <Github className="size-5 shrink-0 text-muted-foreground" />
          <div className="min-w-0">
            {connected ? (
              <>
                <p className="text-sm font-medium">
                  Connected as {installations.map((i) => i.account_login).join(', ')}
                </p>
                <p className="text-sm text-muted-foreground">
                  Private repositories you granted access to can be scanned.
                </p>
              </>
            ) : (
              <>
                <p className="text-sm font-medium">GitHub not connected</p>
                <p className="text-sm text-muted-foreground">
                  Connect to scan private repositories. Public ones work without it.
                </p>
              </>
            )}
          </div>
        </div>

        {/* A real link, not a fetch: the flow leaves for GitHub's consent
            screen and returns through the setup redirect. */}
        <Button asChild variant={connected ? 'outline' : 'default'} className="shrink-0">
          <a href={installUrl()}>{connected ? 'Manage access' : 'Connect GitHub'}</a>
        </Button>
      </CardContent>
    </Card>
  );
}
