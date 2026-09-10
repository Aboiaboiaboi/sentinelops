/**
 * The 33 checks, for the public /how-it-works page.
 *
 * Source of truth: backend/app/scanners/<category>/scanner.py, each
 * module's CHECKS tuple (CheckSpec(id, title) pairs). If a check is added,
 * renamed, or moved between categories there, this file has to be updated
 * by hand — nothing here imports from the backend, so nothing catches
 * drift automatically.
 *
 * Deliberately no per-check point values. CheckSpec only carries id and
 * title; point impacts are separate private constants in each scanner
 * module, applied on failure rather than declared on the check. Five
 * checks (the tool-backed ones) scale at runtime with severity/volume, and
 * Deployment's ten impacts don't sum to its 17 weight either — it has two
 * mutually exclusive scoring paths depending on what the repo has.
 * Publishing numbers here would invite a reader to add up a column that
 * doesn't add up — the category weight is real and shown; individual
 * check weights are not.
 */

export type CheckTool = 'Gitleaks' | 'Trivy' | 'Semgrep' | 'Hadolint' | 'Checkov';

export interface Check {
  id: string;
  title: string;
  tool?: CheckTool;
}

export interface CheckCategory {
  category: string;
  weight: number;
  checks: Check[];
}

export const CHECK_CATALOG: CheckCategory[] = [
  {
    category: 'Security',
    weight: 25,
    checks: [
      { id: 'security.credential_files', title: 'No credential files committed' },
      { id: 'security.hardcoded_secrets', title: 'No secrets hardcoded in source', tool: 'Gitleaks' },
      { id: 'security.dependency_vulnerabilities', title: 'No known-vulnerable dependencies', tool: 'Trivy' },
      { id: 'security.code_patterns', title: 'No dangerous code patterns', tool: 'Semgrep' },
      { id: 'security.debug_mode', title: 'Debug mode off' },
      { id: 'security.tls_verification', title: 'TLS verification enabled' },
      { id: 'security.container_secrets', title: 'No secrets in container configuration' },
      { id: 'security.gitignore', title: 'Env files protected by .gitignore' },
    ],
  },
  {
    category: 'Reliability',
    weight: 20,
    checks: [
      { id: 'reliability.health', title: 'Health endpoint' },
      { id: 'reliability.timeouts', title: 'Timeouts on outbound calls' },
      { id: 'reliability.swallowed_errors', title: 'Errors are not discarded' },
      { id: 'reliability.retries', title: 'Retry handling' },
    ],
  },
  {
    category: 'Deployment',
    weight: 17,
    checks: [
      { id: 'deployment.config', title: 'Deployment configuration' },
      { id: 'deployment.image_pinning', title: 'Pinned base images' },
      { id: 'deployment.non_root', title: 'Container drops privileges' },
      { id: 'deployment.healthcheck', title: 'Container healthcheck' },
      { id: 'deployment.dockerignore', title: 'Build context excluded' },
      { id: 'deployment.signal_handling', title: 'Container receives stop signals' },
      { id: 'deployment.privileged', title: 'Host isolation preserved' },
      { id: 'deployment.ci', title: 'CI pipeline' },
      { id: 'deployment.dockerfile_lint', title: 'Dockerfile lint findings', tool: 'Hadolint' },
      {
        id: 'deployment.iac_misconfiguration',
        title: 'Infrastructure misconfiguration',
        tool: 'Checkov',
      },
    ],
  },
  {
    category: 'Architecture',
    weight: 14,
    checks: [
      { id: 'architecture.tests', title: 'Automated tests' },
      { id: 'architecture.lockfile', title: 'Dependency locking' },
      { id: 'architecture.file_size', title: 'Reviewable file sizes' },
      { id: 'architecture.layout', title: 'Module organisation' },
      { id: 'architecture.readme', title: 'README' },
    ],
  },
  {
    category: 'Scalability',
    weight: 14,
    checks: [
      { id: 'scalability.in_memory_state', title: 'State kept outside the process' },
      { id: 'scalability.local_storage', title: 'Uploads kept off local disk' },
      { id: 'scalability.connection_pooling', title: 'Database connection pooling' },
    ],
  },
  {
    category: 'Observability',
    weight: 10,
    checks: [
      { id: 'observability.logging', title: 'Logging framework' },
      { id: 'observability.structured_logging', title: 'Machine-readable logs' },
      { id: 'observability.telemetry', title: 'Metrics or error tracking' },
    ],
  },
];

export const TOTAL_CHECK_COUNT = CHECK_CATALOG.reduce((n, c) => n + c.checks.length, 0);
