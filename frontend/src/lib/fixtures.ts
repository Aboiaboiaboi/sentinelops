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
import type { CheckResult, ScanComparison } from '@/types/check';
import type { Finding } from '@/types/finding';
import type { GitHubInstallation, GitHubRepository } from '@/types/github';
import type { CreateProjectInput, Project, UpdateProjectInput, User } from '@/types/project';
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
/**
 * The category outcomes of a *finished* scan.
 *
 * Nothing is `pending` here, and that is not a detail: the worker records every
 * category as completed or failed before it completes the scan, so a finished
 * scan can never still be scanning something. This fixture used to leave
 * observability pending, which made the demo show a completed scan whose
 * legend still advertised "Still scanning" — an impossible state that looked
 * like a UI bug because it was indistinguishable from one.
 *
 * `deployment: failed` stays, because a scan completing with one category
 * missing is real: each runs in its own sandbox and one timing out does not
 * invalidate the rest.
 */
const DEMO_CATEGORY_STATUS: CategoryStatusMap = {
  security: 'completed',
  reliability: 'completed',
  architecture: 'completed',
  deployment: 'failed',
  observability: 'completed',
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
/**
 * Points per category. Every completed category appears: one that is completed
 * but absent here falls back to its full cap, which would quietly contradict
 * the headline. `deployment` is the only one missing, and only because it
 * failed and therefore earned nothing.
 *
 * These sum to DEMO_SCORE. Keep it that way — the demo is the first thing
 * anyone sees, and a chart disagreeing with its own total reads as a bug in
 * the product rather than in the fixture.
 */
const DEMO_CATEGORY_SCORES: Record<string, number> = {
  // 25 less the two security findings below, which are worth 5 each under the
  // v2 rubric. Both numbers move together or the demo shows arithmetic that
  // the real scanner cannot produce.
  security: 15,
  reliability: 16,
  architecture: 17,
  observability: 8,
  scalability: 3,
};

const DEMO_SCORE = Object.values(DEMO_CATEGORY_SCORES).reduce((a, b) => a + b, 0);

/**
 * What the previous scan scored, for the comparison fixture.
 *
 * Derived from the same category numbers so the two cannot drift: it is this
 * scan's categories, with security six points worse, observability two better,
 * and deployment still reporting six before it began failing.
 */
const DEMO_PREVIOUS_SCORE = DEMO_SCORE - 6 + 2 + 6;

interface ScanRecord {
  id: string;
  project_id: string;
  created_at: string;
  /** Epoch ms the scan began. `0` means "long finished" — the seeded scan. */
  startedAt: number;
  /** The user-supplied label, mutable in the demo as it is in the real thing. */
  name?: string | null;
}

let nextId = 1;
const newId = (prefix: string) => `${prefix}_${nextId++}`;

/**
 * The account every fixture record belongs to — `user_id: 'user_demo'` below.
 *
 * Needed once the app checks its session before rendering a protected route: in
 * fixture mode there is no cookie and no API, so an unanswerable `/auth/me`
 * would bounce the demo straight back to the login screen.
 */
const DEMO_USER: User = {
  id: 'user_demo',
  email: 'founder@sentinelops.dev',
  created_at: new Date(Date.now() - 86_400_000 * 30).toISOString(),
};

const projects: Project[] = [
  {
    id: 'proj_demo',
    user_id: 'user_demo',
    name: 'sentinelops-api',
    repository_url: 'https://github.com/acme/sentinelops-api',
    framework: 'FastAPI',
    // Has a completed scan below, so its URL is frozen — the demo shows the
    // locked state, which is the one worth seeing.
    repository_url_editable: false,
    created_at: new Date(Date.now() - 86_400_000 * 9).toISOString(),
  },
  {
    id: 'proj_web',
    user_id: 'user_demo',
    name: 'acme-storefront',
    repository_url: 'https://github.com/acme/acme-storefront',
    framework: 'Next.js',
    repository_url_editable: true,
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
  const base = {
    id: record.id,
    project_id: record.project_id,
    name: record.name ?? null,
    created_at: record.created_at,
    // Set once the simulated scan has run its course, mirroring the worker
    // writing it on every terminal transition.
    completed_at:
      record.startedAt === 0 || Date.now() - record.startedAt >= SIMULATED_SCAN_MS
        ? record.created_at
        : null,
  };

  // Only known once the checkout exists, so a scan still running reports none —
  // same as the real worker, which writes it after the clone.
  const noCommit = {
    commit_sha: null,
    commit_message: null,
    commit_author: null,
    committed_at: null,
  };

  // Fixture scans never fail — the demo exercises the happy path — so the
  // failure fields are always null. They are spread rather than omitted
  // because the client's type has no optional keys.
  const noFailure = {
    error_category: null,
    error_detail: null,
    error_hint: null,
  };

  if (elapsed >= SIMULATED_SCAN_MS) {
    return {
      ...base,
      ...noFailure,
      status: 'completed',
      score: DEMO_SCORE,
      scoring_version: 'v2',
      category_status: DEMO_CATEGORY_STATUS,
      category_scores: DEMO_CATEGORY_SCORES,
      category_max_scores: CATEGORY_MAX_SCORES,
      commit_sha: '9f2c1ab4e7d05836a1bb42c9f0e7d2318ac54b60',
      commit_message: 'feat(api): add rate limiting to the auth endpoints',
      commit_author: 'Ada Lovelace',
      committed_at: new Date(Date.now() - 3_600_000).toISOString(),
    };
  }

  const halfway = elapsed >= SIMULATED_SCAN_MS / 2;
  return {
    ...base,
    ...noCommit,
    ...noFailure,
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
  /** Always signed in. There is no cookie to expire in a demo. */
  getSession: () => delay(DEMO_USER),

  logout: () => delay(null),

  listProjects: () => delay([...projects]),

  getProject: (projectId: string) => delay(requireProject(projectId)),

  createProject(input: CreateProjectInput) {
    const created: Project = {
      id: newId('proj'),
      user_id: 'user_demo',
      name: input.name,
      repository_url: input.repository_url,
      framework: null,
      // Nothing has scanned it yet.
      repository_url_editable: true,
      created_at: new Date().toISOString(),
    };
    projects.unshift(created);
    return delay(created);
  },

  updateProject(projectId: string, input: UpdateProjectInput) {
    const project = requireProject(projectId);
    if (input.repository_url !== undefined && input.repository_url !== project.repository_url) {
      // Mirrors the API: the demo projects already have scans, so their URL is
      // frozen and the fixture must refuse it the same way rather than letting
      // the demo do something the real thing forbids.
      if (!project.repository_url_editable) {
        throw new ApiError(
          409,
          'This project has a completed scan, so its repository URL is fixed.',
        );
      }
      project.repository_url = input.repository_url;
      project.framework = null;
    }
    if (input.name !== undefined) project.name = input.name;
    return delay(project);
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

  renameScan(scanId: string, name: string | null) {
    const record = scans.find((s) => s.id === scanId);
    if (!record) throw new ApiError(404, 'Scan not found');
    record.name = (name ?? '').trim() || null;
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

  // An improvement with one regression, so the comparison shows both
  // directions and the "no longer assessed" case that is not a drop.
  getComparison: (scanId: string): Promise<ScanComparison> =>
    delay(
      isFinished(scanId)
        ? {
            previous_scan_id: 'scan_previous',
            previous_created_at: new Date(Date.now() - 86_400_000).toISOString(),
            previous_score: DEMO_PREVIOUS_SCORE,
            comparable: true,
            reason: null,
            score_delta: DEMO_SCORE - DEMO_PREVIOUS_SCORE,
            // Every entry agrees with DEMO_CATEGORY_SCORES above. The version
            // before this claimed scalability was "no longer assessed" while
            // the chart on the same page showed it scoring 3/10 — a demo that
            // contradicts itself reads as a broken product.
            //
            // Deployment is the honest "no longer assessed" case, because it
            // really is absent from the current scores: it failed. That also
            // makes the total go *down* while two categories improved, which
            // is exactly the nuance worth demonstrating.
            categories: [
              { category: 'security', previous: 8, current: 14, delta: 6 },
              { category: 'observability', previous: 10, current: 8, delta: -2 },
              { category: 'deployment', previous: 6, current: null, delta: null },
            ],
            checks: [
              {
                id: 'observability.telemetry',
                title: 'Metrics or error tracking',
                category: 'observability',
                previous_outcome: 'passed',
                current_outcome: 'failed',
              },
              {
                id: 'security.debug_mode',
                title: 'Debug mode off',
                category: 'security',
                previous_outcome: 'failed',
                current_outcome: 'passed',
              },
            ],
          }
        : {
            previous_scan_id: null,
            previous_created_at: null,
            previous_score: null,
            comparable: false,
            reason: null,
            score_delta: null,
            categories: [],
            checks: [],
          },
    ),

  // A slice of each outcome, so the disclosure demonstrates the distinction it
  // exists for: a skipped check is not a passed one.
  listChecks: (scanId: string): Promise<CheckResult[]> =>
    delay(
      isFinished(scanId)
        ? [
            {
              id: 'security.credential_files',
              category: 'security',
              title: 'No credential files committed',
              outcome: 'passed',
              reason: null,
            },
            {
              id: 'security.debug_mode',
              category: 'security',
              title: 'Debug mode off',
              outcome: 'failed',
              reason: null,
            },
            {
              // Errored, not skipped: the question applies perfectly well to
              // this repository, we just have not answered it. Modelled here
              // because it is a state the backend really produces today — both
              // tool-backed checks report it until Trivy and Semgrep land.
              id: 'security.dependency_vulnerabilities',
              category: 'security',
              title: 'No known-vulnerable dependencies',
              outcome: 'errored',
              reason: 'this check is not implemented yet',
            },
            {
              id: 'reliability.health',
              category: 'reliability',
              title: 'Health endpoint',
              outcome: 'passed',
              reason: null,
            },
            {
              id: 'reliability.retries',
              category: 'reliability',
              title: 'Retry handling',
              outcome: 'skipped',
              reason: 'no outbound calls were found to retry',
            },
            {
              id: 'scalability.local_storage',
              category: 'scalability',
              title: 'Uploads kept off local disk',
              outcome: 'skipped',
              reason: 'an object storage client is in use, so local writes are staging',
            },
          ]
        : [],
    ),

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
    title: 'Credentials are committed in the code',
    description:
      'Gitleaks found what it recognises as live credential formats in 3 places across 2 kinds, starting with config/settings.py (generic api key). Detected: generic api key, aws access token. A committed credential is readable by everyone with repository access, survives in git history after the file is deleted, and ships inside every build artefact made from this code. The values themselves are redacted from this report — SentinelOps never stores them.',
    recommendation:
      'Rotate every credential found, before anything else: removing one without rotating it fixes nothing, because the old value is still in history. Then load them from the environment or a secrets manager, and add a pre-commit secret scanner so the next one is caught before it lands.',
    score_impact: 5,
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
    score_impact: 5,
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
