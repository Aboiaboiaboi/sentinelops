import { cn } from '@/lib/utils';
import { scoreToGrade } from '@/types/scan';

/**
 * Radial score dial, drawn as a plain SVG arc rather than a Recharts
 * RadialBarChart — it's a single static arc, so a chart library adds weight and
 * a container-sizing dependency for nothing.
 */

const SIZE = 132;
const STROKE = 10;
const RADIUS = (SIZE - STROKE) / 2;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;
/** Leaves a gap at the bottom so the arc reads as a gauge, not a ring. */
const ARC_FRACTION = 0.78;

function scoreColor(score: number): string {
  if (score >= 80) return 'var(--scan-completed)';
  if (score >= 60) return 'var(--severity-medium)';
  return 'var(--destructive)';
}

export interface ScoreGaugeProps {
  /** 0–100, or null while the scan is still running. */
  score: number | null;
  className?: string;
}

export function ScoreGauge({ score, className }: ScoreGaugeProps) {
  const clamped = score === null ? 0 : Math.max(0, Math.min(100, score));
  const arcLength = CIRCUMFERENCE * ARC_FRACTION;
  const filled = arcLength * (clamped / 100);

  return (
    <div className={cn('relative inline-flex items-center justify-center', className)}>
      <svg
        width={SIZE}
        height={SIZE}
        viewBox={`0 0 ${SIZE} ${SIZE}`}
        role="img"
        aria-label={
          score === null ? 'Score not yet available' : `Score ${score} out of 100`
        }
      >
        {/* Rotated so the gap sits centred at the bottom. */}
        <g transform={`rotate(${90 + (1 - ARC_FRACTION) * 180} ${SIZE / 2} ${SIZE / 2})`}>
          <circle
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={RADIUS}
            fill="none"
            stroke="var(--muted)"
            strokeWidth={STROKE}
            strokeLinecap="round"
            strokeDasharray={`${arcLength} ${CIRCUMFERENCE}`}
          />
          {score !== null && (
            <circle
              cx={SIZE / 2}
              cy={SIZE / 2}
              r={RADIUS}
              fill="none"
              stroke={scoreColor(clamped)}
              strokeWidth={STROKE}
              strokeLinecap="round"
              strokeDasharray={`${filled} ${CIRCUMFERENCE}`}
            />
          )}
        </g>
      </svg>

      <div className="absolute flex flex-col items-center">
        <span className="text-3xl font-semibold tabular-nums">
          {score === null ? '—' : score}
        </span>
        <span className="text-xs text-muted-foreground">
          {score === null ? 'pending' : `Grade ${scoreToGrade(score)}`}
        </span>
      </div>
    </div>
  );
}
