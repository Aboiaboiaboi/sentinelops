import { useQuery } from '@tanstack/react-query';
import { listInstallations, listRepositories } from '@/api/github';
import { USE_FIXTURES, store } from '@/lib/fixtures';

export const githubKeys = {
  installations: ['github', 'installations'] as const,
  repositories: ['github', 'repositories'] as const,
};

export function useGitHubInstallations() {
  return useQuery({
    queryKey: githubKeys.installations,
    queryFn: USE_FIXTURES ? store.listInstallations : listInstallations,
  });
}

/**
 * Only fetched once the picker is actually opened.
 *
 * Every call costs GitHub API round trips against the user's rate limit — one
 * per installation, more for a big organisation — so this must not run on
 * every dashboard render.
 */
export function useGitHubRepositories(enabled: boolean) {
  return useQuery({
    queryKey: githubKeys.repositories,
    queryFn: USE_FIXTURES ? store.listRepositories : listRepositories,
    enabled,
    // The grant changes on GitHub, not here, so refetching on every focus
    // would spend rate limit to learn nothing.
    staleTime: 60_000,
  });
}
