import { Lock } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useGitHubRepositories } from '@/hooks/useGitHub';
import type { GitHubRepository } from '@/types/github';

interface RepositoryPickerProps {
  open: boolean;
  onPick: (repository: GitHubRepository) => void;
}

/**
 * The repositories a connected GitHub account grants, as a pick list.
 *
 * Only fetches when open — see useGitHubRepositories for why. The free-text URL
 * field stays alongside this: most repositories people want to scan are public
 * and need no connection at all.
 */
export function RepositoryPicker({ open, onPick }: RepositoryPickerProps) {
  const { data: repositories, isPending, isError, error } = useGitHubRepositories(open);

  if (!open) return null;
  if (isPending) return <Skeleton className="h-24 w-full" />;

  if (isError) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {error.message}
      </p>
    );
  }

  if (!repositories || repositories.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No repositories available. Use “Manage access” above to grant some.
      </p>
    );
  }

  return (
    <ul className="max-h-56 space-y-1 overflow-y-auto rounded-md border p-1">
      {repositories.map((repository) => (
        <li key={repository.full_name}>
          {/* The label is explicit rather than left to be computed from the
              contents: with the lock icon carrying its own aria-label the
              button exposed no accessible name at all in the tree, so a
              screen reader announced an unnamed button. The icon is decorative
              now and "private" is said in words. */}
          <Button
            type="button"
            variant="ghost"
            aria-label={`Select ${repository.full_name}${repository.private ? ' (private)' : ''}`}
            className="h-auto w-full justify-start gap-2 px-2 py-1.5 font-normal"
            onClick={() => onPick(repository)}
          >
            {repository.private && (
              <Lock className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
            )}
            <span className="truncate">{repository.full_name}</span>
          </Button>
        </li>
      ))}
    </ul>
  );
}
