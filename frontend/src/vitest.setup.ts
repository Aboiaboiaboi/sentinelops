import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';

// Vitest runs without globals (see vite.config.ts), so Testing Library's
// automatic cleanup — which hooks a global afterEach — never registers itself.
// Unmounting between tests has to be wired up explicitly instead.
afterEach(() => {
  cleanup();
});

/**
 * jsdom does not implement ResizeObserver, and Recharts' ResponsiveContainer
 * constructs one on mount — without this, rendering any chart throws.
 *
 * A no-op is the right stub rather than a fake that reports a size: jsdom has no
 * layout engine, so a chart can never have real dimensions here. Chart tests
 * therefore assert against the accessible text, not the SVG. See
 * CategoryBreakdownChart.test.tsx.
 */
class ResizeObserverStub implements ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver ??= ResizeObserverStub;
