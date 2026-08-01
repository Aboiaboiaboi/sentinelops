export interface User {
  id: string;
  email: string;
  created_at: string;
  // No password_hash — the backend's response schemas deliberately exclude it.
}

export interface Project {
  id: string;
  user_id: string;
  name: string;
  repository_url: string;
  /** Detected by the scanner, not supplied at creation. */
  framework: string | null;
  /**
   * Whether the URL can still be changed — false once a scan has completed or
   * while one is running. Sent so the field can be disabled with a reason
   * rather than rejecting a save the user had no way to anticipate.
   */
  repository_url_editable: boolean;
  created_at: string;
}

/** Body of POST /projects. */
export interface CreateProjectInput {
  name: string;
  repository_url: string;
}

/**
 * Body of PATCH /projects/{id}.
 *
 * Every field optional: what is omitted is left alone, so renaming never
 * touches the URL.
 */
export interface UpdateProjectInput {
  name?: string;
  repository_url?: string;
}

export interface Credentials {
  email: string;
  password: string;
}
