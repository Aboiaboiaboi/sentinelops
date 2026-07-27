import { describe, expect, it } from 'vitest';
import { isScanFinished, scanAnnouncement, scoreToGrade } from './scan';

describe('scoreToGrade', () => {
  // Every boundary, because an off-by-one here silently mislabels a report.
  it.each([
    [100, 'A'],
    [90, 'A'],
    [89, 'B'],
    [80, 'B'],
    [79, 'C'],
    [70, 'C'],
    [69, 'D'],
    [60, 'D'],
    [59, 'F'],
    [0, 'F'],
  ])('grades %i as %s', (score, grade) => {
    expect(scoreToGrade(score)).toBe(grade);
  });

  it('renders an em dash while the score is still null', () => {
    expect(scoreToGrade(null)).toBe('—');
  });
});

describe('isScanFinished', () => {
  it('treats completed and failed as terminal', () => {
    expect(isScanFinished('completed')).toBe(true);
    expect(isScanFinished('failed')).toBe(true);
  });

  // These three keep the polling loop in useScan alive; a false positive here
  // would stop polling a scan that is still running.
  it('treats pending, running and undefined as in flight', () => {
    expect(isScanFinished('pending')).toBe(false);
    expect(isScanFinished('running')).toBe(false);
    expect(isScanFinished(undefined)).toBe(false);
  });
});

describe('scanAnnouncement', () => {
  it('states the score and grade once the scan completes', () => {
    expect(scanAnnouncement({ status: 'completed', score: 68 }, 4, 6)).toBe(
      'Scan completed. Score 68 out of 100, grade D. 4 of 6 categories reported.',
    );
  });

  it('reports progress while running', () => {
    expect(scanAnnouncement({ status: 'running', score: null }, 2, 6)).toBe(
      'Scan in progress. 2 of 6 categories reported.',
    );
  });

  it('says a failed scan produced no score', () => {
    expect(scanAnnouncement({ status: 'failed', score: null }, 0, 6)).toMatch(/failed/);
  });

  it('handles a completed scan that somehow has no score', () => {
    expect(scanAnnouncement({ status: 'completed', score: null }, 6, 6)).toBe(
      'Scan completed without a score.',
    );
  });

  // Each status must produce distinct speech, or a transition announces nothing.
  it('gives every status its own text', () => {
    const texts = (['pending', 'running', 'completed', 'failed'] as const).map((status) =>
      scanAnnouncement({ status, score: status === 'completed' ? 68 : null }, 4, 6),
    );
    expect(new Set(texts).size).toBe(4);
  });
});
