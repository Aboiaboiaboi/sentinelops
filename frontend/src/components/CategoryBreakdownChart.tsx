import {
  Bar,
  BarChart,
  Cell,
  LabelList,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from 'recharts';
import { categoryLabel } from '@/lib/categories';
import { cn } from '@/lib/utils';
import type { CategoryScore, CategoryStatus } from '@/types/scan';

/**
 * Horizontal category breakdown.
 *
 * The three category states are the whole point of this component and each gets
 * a visually distinct treatment:
 *
 *   completed → primary colour, real percentage of the weight cap, "{score}/{max}"
 *   pending   → amber + pulsing, full-width indeterminate track, "Scanning…"
 *   failed    → flat grey, full-width indeterminate track, "Not reported"
 *
 * pending and failed both render full width because neither has a score to show;
 * a partial-width bar would imply a measurement that does not exist. They are
 * separated by hue and motion, never by length. A scan can legitimately complete
 * with some categories missing (one sandbox timing out does not fail the scan),
 * so "still working" must never be mistakable for "gave up".
 */

interface ChartRow {
  category: string;
  label: string;
  status: CategoryStatus;
  /** Bar length, 0–100. Non-completed rows are full-width placeholders. */
  value: number;
  /** Text drawn on/next to the bar. */
  caption: string;
}

interface StateStyle {
  fill: string;
  /** Caption colour when the caption is drawn on top of the bar. */
  captionFill: string;
  className?: string;
}

const STATE_STYLES: Record<CategoryStatus, StateStyle> = {
  completed: {
    fill: 'var(--scan-completed)',
    captionFill: 'var(--scan-completed-foreground)',
  },
  pending: {
    fill: 'var(--scan-pending)',
    captionFill: 'var(--scan-pending-foreground)',
    className: 'bar-pending',
  },
  failed: {
    fill: 'var(--scan-failed)',
    captionFill: 'var(--scan-failed-foreground)',
  },
};

function toRow(entry: CategoryScore): ChartRow {
  const label = categoryLabel(entry.category);

  if (entry.status === 'completed') {
    const score = entry.score ?? 0;
    const pct = entry.maxScore > 0 ? (score / entry.maxScore) * 100 : 0;
    return {
      category: entry.category,
      label,
      status: 'completed',
      value: Math.max(0, Math.min(100, pct)),
      caption: `${score}/${entry.maxScore}`,
    };
  }

  return {
    category: entry.category,
    label,
    status: entry.status,
    value: 100,
    caption: entry.status === 'pending' ? 'Scanning…' : 'Not reported',
  };
}

/**
 * Places the caption inside the bar when there is room, outside when there is
 * not — so a category scoring 3/25 stays readable instead of clipping.
 *
 * Inside the bar the caption takes that state's foreground rather than a fixed
 * white: the completed and pending fills are light, so white would vanish on
 * them. `rows` is passed in and indexed because Recharts hands a LabelList
 * renderer the row's position but not the row itself.
 */
function BarCaption(props: {
  x?: number;
  y?: number;
  width?: number;
  height?: number;
  value?: string | number;
  index?: number;
  rows?: ChartRow[];
}) {
  const { x = 0, y = 0, width = 0, height = 0, value, index = 0, rows = [] } = props;
  const text = String(value ?? '');
  const fitsInside = width > 76;
  const status = rows[index]?.status ?? 'completed';

  return (
    <text
      x={fitsInside ? x + width - 10 : x + width + 8}
      y={y + height / 2}
      textAnchor={fitsInside ? 'end' : 'start'}
      dominantBaseline="central"
      className="text-xs font-medium"
      fill={
        fitsInside ? STATE_STYLES[status].captionFill : 'var(--muted-foreground)'
      }
    >
      {text}
    </text>
  );
}

function LegendSwatch({ status, children }: { status: CategoryStatus; children: string }) {
  const style = STATE_STYLES[status];
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className={cn('size-2.5 rounded-sm', style.className)}
        style={{ backgroundColor: style.fill }}
      />
      {children}
    </span>
  );
}

export interface CategoryBreakdownChartProps {
  categories: CategoryScore[];
  className?: string;
}

export function CategoryBreakdownChart({
  categories,
  className,
}: CategoryBreakdownChartProps) {
  if (categories.length === 0) {
    return (
      <p className={cn('py-8 text-center text-sm text-muted-foreground', className)}>
        No categories to display yet.
      </p>
    );
  }

  const rows = categories.map(toRow);
  const hasIncomplete = rows.some((r) => r.status !== 'completed');

  return (
    <div className={className}>
      {/* Recharts renders SVG that screen readers can't meaningfully traverse,
          so the same information is exposed as text. */}
      <ul className="sr-only">
        {rows.map((row) => (
          <li key={row.category}>
            {row.label}: {row.caption}
          </li>
        ))}
      </ul>

      <div aria-hidden="true">
        <ResponsiveContainer width="100%" height={Math.max(180, rows.length * 44)}>
          <BarChart
            data={rows}
            layout="vertical"
            margin={{ top: 4, right: 76, bottom: 4, left: 4 }}
            barCategoryGap={10}
          >
            <XAxis type="number" domain={[0, 100]} hide />
            <YAxis
              type="category"
              dataKey="label"
              width={104}
              tickLine={false}
              axisLine={false}
              tick={{ fontSize: 13, fill: 'var(--muted-foreground)' }}
            />
            <Bar dataKey="value" radius={4} isAnimationActive={false}>
              {rows.map((row) => {
                const style = STATE_STYLES[row.status];
                return (
                  <Cell
                    key={row.category}
                    fill={style.fill}
                    className={style.className}
                  />
                );
              })}
              <LabelList dataKey="caption" content={<BarCaption rows={rows} />} />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 pl-1 text-xs text-muted-foreground">
        <LegendSwatch status="completed">Reported</LegendSwatch>
        <LegendSwatch status="pending">Still scanning</LegendSwatch>
        <LegendSwatch status="failed">Not reported</LegendSwatch>
      </div>

      {hasIncomplete && (
        <p className="mt-3 text-xs leading-relaxed text-muted-foreground">
          Categories run in isolated sandboxes. One timing out or failing does not
          invalidate the rest — the score above covers only the categories that
          reported.
        </p>
      )}
    </div>
  );
}
