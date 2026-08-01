import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ScanChecks } from './ScanChecks';
import type { CheckResult } from '@/types/check';

const checks: CheckResult[] = [
  {
    id: 'security.gitignore',
    category: 'security',
    title: 'Env files protected',
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
    id: 'security.hardcoded_secrets',
    category: 'security',
    title: 'No secrets in source',
    outcome: 'errored',
    reason: 'the secret scanner could not be started',
  },
  {
    id: 'reliability.health',
    category: 'reliability',
    title: 'Health endpoint',
    outcome: 'skipped',
    reason: 'only asked of something that serves traffic',
  },
];

vi.mock('@/hooks/useFindings', () => ({
  useChecks: () => ({ data: checks, isPending: false, isError: false }),
}));

/**
 * The contract here is honesty about outcomes. A check we failed to run must be
 * visibly different from one that did not apply — collapsing the two is exactly
 * what the `errored` outcome was added to prevent.
 */
describe('ScanChecks', () => {
  async function open() {
    render(<ScanChecks scanId="s1" />);
    await userEvent.click(screen.getByRole('button', { name: /what was checked/i }));
  }

  it('names every outcome present in the summary', async () => {
    await open();

    expect(screen.getByText('1 passed · 1 failed · 1 errored')).toBeInTheDocument();
    expect(screen.getByText('0 passed · 1 skipped')).toBeInTheDocument();
  });

  it('distinguishes a check that could not run from one that did not apply', async () => {
    await open();

    // Both carry a reason, so the reason alone cannot be the signal — the
    // outcome announced to a screen reader is what has to differ.
    expect(screen.getByText(/the secret scanner could not be started/)).toBeInTheDocument();
    expect(screen.getByText('(errored)')).toBeInTheDocument();
    expect(screen.getByText('(skipped)')).toBeInTheDocument();
  });

  it('does not count an errored check as passed', async () => {
    await open();

    expect(screen.queryByText(/2 passed/)).not.toBeInTheDocument();
  });
});
