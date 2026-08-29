# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1: builder — compiles the SPA to static files.
#
# `npm ci` rather than `npm install`, so the image is built from exactly what
# package-lock.json pins rather than whatever a floating range resolves to
# today — the same reason the backend's build uses `uv sync --frozen`.
# ---------------------------------------------------------------------------
FROM node:22-alpine AS builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

# Named explicitly rather than `COPY . .` — the same reasoning
# backend/Dockerfile gives for copying `app` rather than the whole build
# context: it is what keeps a stray local file (`.env.local`, a `dist/` from a
# previous build) out of the image without depending on `.dockerignore` being
# read correctly, and it is narrower than what SentinelOps's own deployment
# scanner considers a "full context copy" finding.
COPY index.html vite.config.ts tsconfig.json tsconfig.app.json tsconfig.node.json ./
COPY src ./src
# tsc -b && vite build — the same command `npm run build` runs locally, so a
# build that fails here would have failed on a developer's machine too.
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: runtime — Caddy, serving the build and proxying the API.
#
# One container does both jobs deliberately. Phase 5's single-origin decision
# (06-context-handoff.md / 10-phase5-plan.md §3.1) is why: the auth cookie is
# `SameSite=Lax`, which is not sent on a cross-site request, so the SPA and the
# API must appear to the browser as one origin. A Caddyfile rewriting `/api/**`
# to the backend service does that with no CORS configuration and no second
# certificate — Caddy also obtains and renews the TLS certificate for whatever
# domain it is given, which is the other half of `cookie_secure`.
# ---------------------------------------------------------------------------
FROM caddy:2-alpine AS runtime

COPY --from=builder /app/dist /srv
COPY Caddyfile /etc/caddy/Caddyfile
