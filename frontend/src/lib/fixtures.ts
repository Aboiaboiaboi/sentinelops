/**
 * TEMPORARY — delete once the backend is serving real data.
 *
 * Exists so the whole app can be reviewed and demoed before any API exists. The
 * seeded scan deliberately covers all three category states at once, since the
 * whole risk in that screen is `pending` and `failed` looking alike.
 *
 * Enable by setting VITE_USE_FIXTURES=true in .env.local.
 *
 * The switch is consumed in `hooks/`, not in pages — every page stays unaware
 * that fixtures exist. Deleting this file plus the `USE_FIXTURES ? … : …` lines
 * in hooks/ removes fixture mode entirely.
 */
import { ApiError } from '@/api/client';
import type { Finding } from '@/types/finding';
import type { GitHubInstallation, GitHubRepository } from '@/types/github';
import type { CreateProjectInput, Project } from '@/types/project';
import type { CategoryStatusMap, ScanSummary } from '@/types/scan';

export const USE_FIXTURES = import.meta.env.VITE_USE_FIXTURES === 'true';

/** How long a freshly started fixture scan takes to "run". */
const SIMULATED_SCAN_MS = 6_000;

/** Imitates network latency, so loading states are actually visible in a demo. */
const LATENCY_MS = 250;

function delay<T>(value: T): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), LATENCY_MS));
}

/** The finished scan's per-category outcome — all three states at once. */
const DEMO_CATEGORY_STATUS: CategoryStatusMap = {
  security: 'completed',
  reliability: 'completed',
  architecture: 'completed',
  deployment: 'failed',
  observability: 'pending',
  scalability: 'completed',
};

/**
 * The rubric the API reports alongside every scan. Mirrored here so fixture
 * mode renders bars the same way the real thing does.
 */
const CATEGORY_MAX_SCORES: Record<string, number> = {
  security: 25,
  reliability: 20,
  architecture: 20,
  deployment: 15,
  observability: 10,
  scalability: 10,
};

/** Per-category points for the finished scan. Only the categories that
 * completed appear — a category that did not report has no score. */
const DEMO_CATEGORY_SCORES: Record<string, number> = {
  security: 14,
  reliability: 16,
  architecture: 17,
  scalability: 3,
};

interface ScanRecord {
  id: string;
  project_id: string;
  created_at: string;
  /** Epoch ms the scan began. `0` means "long finished" — the seeded scan. */
  startedAt: number;
}

let nextId = 1;
const newId = (prefix: string) => `${prefix}_${nextId++}`;

const projects: Project[] = [
  {
    id: 'proj_demo',
    user_id: 'user_demo',
    name: 'sentinelops-api',
    repository_url: 'https://github.com/acme/sentinelops-api',
    framework: 'FastAPI',
    created_at: new Date(Date.now() - 86_400_000 * 9).toISOString(),
  },
  {
    id: 'proj_web',
    user_id: 'user_demo',
    name: 'acme-storefront',
    repository_url: 'https://github.com/acme/acme-storefront',
    framework: 'Next.js',
    created_at: new Date(Date.now() - 86_400_000 * 2).toISOString(),
  },
];

const scans: ScanRecord[] = [
  {
    id: 'scan_demo',
    project_id: 'proj_demo',
    created_at: new Date(Date.now() - 3_600_000).toISOString(),
    startedAt: 0,
  },
];

/**
 * Projects a record to the shape `GET /scans/{id}` returns, advancing a running
 * scan through its stages by elapsed time. This is what makes "Run scan"
 * exercise the real polling loop — and the pending → completed transition —
 * rather than jumping straight to a finished result.
 */
function toSummary(record: ScanRecord): ScanSummary {
  const elapsed = record.startedAt === 0 ? Infinity : Date.now() - record.startedAt;
  const base = { id: record.id, project_id: record.project_id, created_at: record.created_at };

  if (elapsed >= SIMULATED_SCAN_MS) {
    return {
      ...base,
      status: 'completed',
      score: 68,
      scoring_version: 'v1',
      category_status: DEMO_CATEGORY_STATUS,
      category_scores: DEMO_CATEGORY_SCORES,
      category_max_scores: CATEGORY_MAX_SCORES,
    };
  }

  const halfway = elapsed >= SIMULATED_SCAN_MS / 2;
  return {
    ...base,
    status: 'running',
    score: null,
    scoring_version: null,
    category_status: {
      security: halfway ? 'completed' : 'pending',
      reliability: halfway ? 'completed' : 'pending',
      architecture: 'pending',
      deployment: 'pending',
      observability: 'pending',
      scalability: 'pending',
    },
    // Mid-scan the API has not written points yet, so a category that has just
    // completed renders at its cap until the scan finishes. Matching that here
    // keeps fixture mode honest about what the real thing looks like.
    category_scores: {},
    category_max_scores: CATEGORY_MAX_SCORES,
  };
}

function requireProject(projectId: string): Project {
  const found = projects.find((p) => p.id === projectId);
  if (!found) throw new ApiError(404, 'Project not found');
  return found;
}

function isFinished(scanId: string): boolean {
  const record = scans.find((s) => s.id === scanId);
  return record ? toSummary(record).status === 'completed' : false;
}

/**
 * Mirrors the api/ surface one-for-one, ApiError failures included, so the hooks
 * calling it cannot tell the difference between this and a real backend.
 */
export const store = {
  listProjects: () => delay([...projects]),

  getProject: (projectId: string) => delay(requireProject(projectId)),

  createProject(input: CreateProjectInput) {
    const created: Project = {
      id: newId('proj'),
      user_id: 'user_demo',
      name: input.name,
      repository_url: input.repository_url,
      framework: null,
      created_at: new Date().toISOString(),
    };
    projects.unshift(created);
    return delay(created);
  },

  deleteProject(projectId: string) {
    const index = projects.findIndex((p) => p.id === projectId);
    if (index === -1) throw new ApiError(404, 'Project not found');
    projects.splice(index, 1);
    // Scans belong to the project; the backend cascades, so this does too.
    for (let i = scans.length - 1; i >= 0; i -= 1) {
      if (scans[i].project_id === projectId) scans.splice(i, 1);
    }
    return delay(undefined);
  },

  listScans(projectId: string) {
    requireProject(projectId);
    return delay(
      scans
        .filter((s) => s.project_id === projectId)
        .map(toSummary)
        .sort((a, b) => b.created_at.localeCompare(a.created_at)),
    );
  },

  getScan(scanId: string) {
    const record = scans.find((s) => s.id === scanId);
    if (!record) throw new ApiError(404, 'Scan not found');
    return delay(toSummary(record));
  },

  startScan(projectId: string) {
    requireProject(projectId);
    const record: ScanRecord = {
      id: newId('scan'),
      project_id: projectId,
      created_at: new Date().toISOString(),
      startedAt: Date.now(),
    };
    scans.unshift(record);
    return delay(toSummary(record));
  },

  listFindings: (scanId: string) =>
    delay(isFinished(scanId) ? [...fixtureFindings] : []),

  // A connected account with one private and one public repository, so the
  // picker and its private badge are visible without a GitHub App configured.
  listInstallations: (): Promise<GitHubInstallation[]> =>
    delay([
      {
        id: 'inst_demo',
        installation_id: 42424242,
        account_login: 'octocat',
        created_at: new Date().toISOString(),
      },
    ]),

  listRepositories: (): Promise<GitHubRepository[]> =>
    delay([
      {
        full_name: 'octocat/internal-billing',
        private: true,
        url: 'https://github.com/octocat/internal-billing.git',
        installation_id: 42424242,
      },
      {
        full_name: 'octocat/public-docs',
        private: false,
        url: 'https://github.com/octocat/public-docs.git',
        installation_id: 42424242,
      },
    ]),
};

/** The seeded finished scan. Exported for tests and direct inspection. */
export const fixtureScan: ScanSummary = toSummary(scans[0]);

export const fixtureFindings: Finding[] = [
  {
    id: 'f1',
    scan_id: 'scan_demo',
    category: 'security',
    severity: 'CRITICAL',
    title: 'Hardcoded secret detected',
    description:
      'An API key was found committed in source at config/settings.py. Anyone with repository access, including via forks and clones, can read it.',
    recommendation:
      'Move the value to an environment variable, rotate the exposed key, and add a pre-commit secret scan.',
    score_impact: 10,
  },
  {
    id: 'f2',
    scan_id: 'scan_demo',
    category: 'security',
    severity: 'HIGH',
    title: 'Dependency with known CVE',
    description:
      'requests 2.19.1 is affected by CVE-2018-18074, which leaks Authorization headers across redirects.',
    recommendation: 'Upgrade to requests >= 2.20.0.',
    score_impact: 6,
  },
  {
    id: 'f3',
    scan_id: 'scan_demo',
    category: 'scalability',
    severity: 'MEDIUM',
    title: 'No connection pooling configured',
    description:
      'The database engine is created without pool sizing, so every worker opens connections unbounded under load.',
    recommendation: 'Set pool_size and max_overflow on the SQLAlchemy engine.',
    score_impact: 4,
  },
  {
    id: 'f4',
    scan_id: 'scan_demo',
    category: 'reliability',
    severity: 'LOW',
    title: 'No health check endpoint',
    description:
      'No /health or /readyz route was found, so orchestrators cannot tell whether the service is ready.',
    recommendation: 'Add a lightweight health endpoint that checks DB connectivity.',
    score_impact: 2,
  },
];
