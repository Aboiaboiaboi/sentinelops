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
  created_at: string;
}

/** Body of POST /projects. */
export interface CreateProjectInput {
  name: string;
  repository_url: string;
}

export interface Credentials {
  email: string;
  password: string;
}
