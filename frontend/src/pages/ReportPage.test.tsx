import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import ReportPage from './ReportPage';
import type { ScanStatus, ScanSummary } from '@/types/scan';

const baseScan: ScanSummary = {
  id: 'scan_demo',
  project_id: 'project_demo',
  name: null,
  status: 'completed',
  score: 88,
  scoring_version: 'v2',
  category_status: {},
  category_scores: {},
  category_max_scores: {},
  error_category: null,
  error_detail: null,
  error_hint: null,
  commit_sha: null,
  commit_message: null,
  commit_author: null,
  committed_at: null,
  created_at: '2026-01-01T00:00:00Z',
  completed_at: '2026-01-01T00:05:00Z',
};

let scan: ScanSummary = baseScan;

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useParams: () => ({ scanId: scan.id }) };
});

vi.mock('@/hooks/useScan', () => ({
  useScan: () => ({ data: scan, isPending: false, isError: false }),
}));

vi.mock('@/hooks/useFindings', () => ({
  useFindings: () => ({ data: [], isPending: false, isError: false }),
}));

function renderAt(status: ScanStatus) {
  scan = { ...baseScan, status, score: status === 'completed' ? baseScan.score : null };
  render(
    <MemoryRouter>
      <ReportPage />
    </MemoryRouter>,
  );
  return screen.getByRole(status === 'pending' || status === 'running' ? 'button' : 'link', {
    name: /download pdf/i,
  });
}

describe('ReportPage download button', () => {
  it('links to the report once the scan has completed', () => {
    const control = renderAt('completed');

    expect(control).toHaveAttribute('href', expect.stringContaining(`/scans/${scan.id}/report`));
  });

  it('links to the report for a failed scan too', () => {
    // A failed scan's report carries the failure and the hint, which is the
    // document somebody actually wants in that case — and the endpoint serves
    // it, so the button must not disagree with the API.
    const control = renderAt('failed');

    expect(control).toHaveAttribute('href', expect.stringContaining('/report'));
  });

  it.each<ScanStatus>(['pending', 'running'])(
    'offers no download while the scan is %s',
    (status) => {
      // The endpoint answers 409 for an unfinished scan. Offering a download
      // that cannot work sends the user to a browser error page with no
      // explanation, which is the one outcome worse than an unavailable button.
      const control = renderAt(status);

      expect(control).toBeDisabled();
      expect(control).not.toHaveAttribute('href');
    },
  );

  it('says why the download is unavailable', () => {
    const control = renderAt('running');

    expect(control).toHaveAttribute('title', expect.stringMatching(/once the scan finishes/i));
  });
});
