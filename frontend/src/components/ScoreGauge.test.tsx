import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ScoreGauge } from './ScoreGauge';

describe('ScoreGauge', () => {
  it('labels the arc for screen readers', () => {
    render(<ScoreGauge score={68} />);
    expect(screen.getByRole('img', { name: 'Score 68 out of 100' })).toBeInTheDocument();
  });

  it('says the score is unavailable rather than reporting zero', () => {
    render(<ScoreGauge score={null} />);

    expect(screen.getByRole('img', { name: 'Score not yet available' })).toBeInTheDocument();
    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.getByText('pending')).toBeInTheDocument();
  });

  it('shows the grade alongside the number', () => {
    render(<ScoreGauge score={95} />);
    expect(screen.getByText('Grade A')).toBeInTheDocument();
  });
});
