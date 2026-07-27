# SentinelOps

Production readiness assessment platform. Point it at an application repository
and it scores that repo across security, reliability, scalability,
infrastructure, CI/CD, observability, and maintainability — then returns
DevOps, DevSecOps, and cloud engineering recommendations for what to fix.

## Status

The frontend is built and runs. The backend is not implemented yet, so the
frontend ships a fixture mode that exercises every screen — including the scan
polling loop — without a server.

## Quick start

```bash
cd frontend
npm install
```

Run it against fixtures, with no backend required:

```bash
echo "VITE_USE_FIXTURES=true" > .env.local
npm run dev
```

Two things worth looking at once it's up: the seeded scan at `/scans/scan_demo`
holds `completed`, `pending`, and `failed` categories at the same time, and
**Run scan** on a project drives the real polling loop rather than jumping
straight to a finished result.

To run against a real API instead, copy `.env.example` to `.env` and point
`VITE_API_URL` at the backend.

## Checks

```bash
cd frontend && npm run typecheck && npm run lint && npm run test && npm run build
```

## Layout

| Path | Contents |
|---|---|
| `frontend/` | React + TypeScript + Tailwind + shadcn/ui client. See its own README. |

## Stack

React, TypeScript, Vite, Tailwind v4, shadcn/ui, TanStack Query, Recharts,
Vitest + Testing Library. Auth rides on an httpOnly cookie, so the API is
proxied same-origin in local dev.

## License

Apache 2.0 — see [LICENSE](LICENSE).
