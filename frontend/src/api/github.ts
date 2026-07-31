import { request } from './client';
import type { GitHubInstallation, GitHubRepository } from '@/types/github';

export function listInstallations(): Promise<GitHubInstallation[]> {
  return request<GitHubInstallation[]>('/github/installations');
}

export function listRepositories(): Promise<GitHubRepository[]> {
  return request<GitHubRepository[]>('/github/repositories');
}

/**
 * Where to send the browser to install the App.
 *
 * A full page navigation, not a fetch: the flow leaves our origin for GitHub's
 * consent screen and comes back through the setup redirect. Fetching it would
 * follow the redirect in the background and land nobody anywhere.
 *
 * Not routed through `request()` for the same reason — it needs a URL, not a
 * response body.
 */
export function installUrl(): string {
  const base = import.meta.env.VITE_API_URL ?? '/api';
  return `${base}/github/install`;
}
