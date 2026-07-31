import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { CategoryBreakdownChart } from './CategoryBreakdownChart';
import { toCategoryScores } from '@/lib/categories';

/**
 * Asserts against the sr-only list, not the SVG: Recharts' ResponsiveContainer
 * measures its parent, and jsdom reports zero, so no bars are drawn here. That
 * list is also the component's actual accessibility contract — the chart itself
 * is aria-hidden, so it is the only thing a screen reader ever sees.
 */
describe('CategoryBreakdownChart', () => {
  const allStates = toCategoryScores({
    category_status: {
      security: 'completed',
      observability: 'pending',
      deployment: 'failed',
    },
    category_scores: { security: 25 },
    category_max_scores: { security: 25, observability: 10, deployment: 15 },
  });

  it('describes every category in text', () => {
    render(<CategoryBreakdownChart categories={allStates} />);

    expect(screen.getByText('Security: 25/25')).toBeInTheDocument();
    expect(screen.getByText('Observability: Scanning…')).toBeInTheDocument();
    expect(screen.getByText('Deployment: Not reported')).toBeInTheDocument();
  });

  // The risk this component exists to manage: a category still running must not
  // read as one that gave up. The two captions have to stay distinct.
  it('gives pending and failed different captions', () => {
    render(<CategoryBreakdownChart categories={allStates} />);

    const pending = screen.getByText(/Observability:/).textContent;
    const failed = screen.getByText(/Deployment:/).textContent;
    expect(pending).not.toBe(failed);
  });

  it('explains the partial score when some category did not report', () => {
    render(<CategoryBreakdownChart categories={allStates} />);
    expect(screen.getByText(/only the categories that reported/)).toBeInTheDocument();
  });

  it('omits that explanation when every category reported', () => {
    const complete = toCategoryScores({
      category_status: { security: 'completed', reliability: 'completed' },
      category_scores: { security: 25, reliability: 20 },
      category_max_scores: { security: 25, reliability: 20 },
    });
    render(<CategoryBreakdownChart categories={complete} />);

    expect(screen.queryByText(/only the categories that reported/)).not.toBeInTheDocument();
  });

  it('falls back to a message rather than an empty chart frame', () => {
    render(<CategoryBreakdownChart categories={[]} />);
    expect(screen.getByText(/No categories to display yet/)).toBeInTheDocument();
  });

  describe('legend', () => {
    const allCompleted = toCategoryScores({
      category_status: { security: 'completed', deployment: 'completed' },
      category_scores: { security: 25, deployment: 10 },
      category_max_scores: { security: 25, deployment: 15 },
    });

    it('does not claim a scan is still scanning once it has finished', () => {
      /* The amber swatch carries an infinite pulse. Rendered unconditionally,
         it kept breathing under a completed scan — advertising a state the
         scan was not in. */
      render(<CategoryBreakdownChart categories={allCompleted} />);

      expect(screen.queryByText('Still scanning')).not.toBeInTheDocument();
    });

    it('omits the legend entirely when every bar looks the same', () => {
      /* One state means no colour encoding to decode; the captions carry it. */
      render(<CategoryBreakdownChart categories={allCompleted} />);

      expect(screen.queryByText('Reported')).not.toBeInTheDocument();
    });

    it('shows only the states actually on the chart', () => {
      const scanning = toCategoryScores({
        category_status: { security: 'completed', observability: 'pending' },
        category_scores: { security: 25 },
        category_max_scores: { security: 25, observability: 10 },
      });

      render(<CategoryBreakdownChart categories={scanning} />);

      expect(screen.getByText('Reported')).toBeInTheDocument();
      expect(screen.getByText('Still scanning')).toBeInTheDocument();
      expect(screen.queryByText('Not reported')).not.toBeInTheDocument();
    });

    it('still explains all three when all three are present', () => {
      render(<CategoryBreakdownChart categories={allStates} />);

      expect(screen.getByText('Reported')).toBeInTheDocument();
      expect(screen.getByText('Still scanning')).toBeInTheDocument();
      expect(screen.getByText('Not reported')).toBeInTheDocument();
    });
  });
});
