/** One installation of the SentinelOps GitHub App on a user's account. */
export interface GitHubInstallation {
  id: string;
  installation_id: number;
  account_login: string;
  created_at: string;
}

/**
 * A repository the App can see.
 *
 * Assembled from GitHub on each request rather than stored, so the picker
 * always reflects what the user currently grants.
 */
export interface GitHubRepository {
  full_name: string;
  private: boolean;
  /** The https clone URL, which is what a created project stores. */
  url: string;
  installation_id: number;
}
