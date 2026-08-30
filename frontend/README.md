# SentinelOps frontend

React + TypeScript + Tailwind v4 + shadcn/ui + TanStack Query + Recharts.

## Running it

```bash
npm install
npm run dev
```

The backend is a real dependency now — run it via `docker compose up -d` at
the repository root (see the root `README.md`) before `npm run dev`. The
fixture-data mode this section used to describe (`lib/fixtures.ts`,
`VITE_USE_FIXTURES`) has been removed now that the real API covers every
screen end to end.

## Checks

```bash
npm run typecheck && npm run lint && npm run test && npm run build
```

Tests are Vitest + Testing Library, co-located as `*.test.ts(x)` under `src/`.
They cover the logic that carries risk — grade boundaries, category/score
mapping, the API error contract (including that `credentials: 'include'` is set
on *every* request), the 401 broadcast, and the accessible text of the chart and
gauge. There are deliberately no markup snapshots.

Chart tests assert against the `sr-only` list rather than the SVG: jsdom has no
layout engine, so Recharts' `ResponsiveContainer` always measures zero. That list
is also the only thing a screen reader sees, since the chart itself is
`aria-hidden`. `src/vitest.setup.ts` stubs `ResizeObserver`, which jsdom lacks and
Recharts requires.

## Layout

| Path | Role |
|---|---|
| `src/api/` | Plain fetch functions. No React — independently testable. |
| `src/hooks/` | TanStack Query wrappers around `api/`. |
| `src/pages/` | One per screen; owns layout and data fetching. |
| `src/components/` | Prop-driven and page-agnostic. |
| `src/components/ui/` | shadcn primitives — regenerable, edit with care. |
| `src/types/` | Mirrors the backend's DB schema. |

`api/client.ts` is the only place `credentials: 'include'` is set — auth rides on
an httpOnly cookie, and one call missing that option fails in a way that is
annoying to trace.

## Theme

One dark theme, no toggle: near-black surfaces, white text, blue as the only
brand hue. All tokens live in `src/index.css` on `:root` — there is no light
palette to keep in sync. `<html class="dark">` is set statically in `index.html`
so any `dark:` utility agrees with those tokens.

Two blues, because one cannot do both jobs:

| Token | Use |
|---|---|
| `--primary` | deep blue **fill** with white text on top (buttons, badges) |
| `--primary-bright` | light blue used **as** text, icons, focus rings, chart marks |

A blue light enough to read against black is too light to sit under white text.
Reach for `primary-bright` whenever the blue *is* the ink.

Severity is the one scale that stays outside the blue family — four levels have
to be separable at a glance — and each `--scan-*` state carries its own
`-foreground`, because those captions are drawn *inside* the bar.

`@media print` at the bottom of `index.css` re-points the same tokens at a
white-paper palette, so `ReportPage` still prints.

## Auth

The JWT cookie is `httpOnly` and unreadable from JS, so `ProtectedRoute`
asks `GET /auth/me` to find out whether a session actually exists before
rendering anything behind it — that's the only way to know up front. Beyond
that initial check, `lib/queryClient.ts` treats any 401 from any request as
"session gone," broadcasts it, and `ProtectedRoute` redirects to sign-in.
Sign-out is a real `POST /auth/logout` call, not just a client-side cookie
drop.

## Decisions worth knowing

- **Tailwind v4**, so there is no `tailwind.config.js` — theme tokens live in
  `src/index.css` under `@theme`.
- `lib/categories.ts` only holds display labels now — category weights and
  scores both come straight from the API (`category_scores`,
  `category_max_scores`), so there's no separate table to keep in sync.
- Added `components/ErrorBoundary.tsx`. React unmounts the whole tree on a render
  throw, which would otherwise leave a blank page with the reason only in the
  console. `errorElement` was not an option — the app uses `<BrowserRouter>` +
  `<Routes>`, not a data router.
- `ScanPage` and `ReportPage` are `lazy()`-loaded (see `App.tsx`). They are the
  only screens that render charts, and Recharts is over half the bundle; splitting
  them takes first load from 648 kB to 260 kB (190 → 83 kB gzipped), so the login
  screen no longer downloads a charting library it never uses. `AppLayout` holds
  the `Suspense` boundary, inside the header, so the chrome stays put while a
  route chunk arrives.
- `npm run typecheck` runs `tsc -b`, not `tsc --noEmit`. With a solution-style
  `tsconfig.json` (`"files": []` plus project references), `--noEmit` silently
  checks **nothing** — build mode is what actually walks the referenced projects.
