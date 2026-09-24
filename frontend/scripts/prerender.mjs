// Runs after `vite build` and the SSR-only build of entry-prerender.tsx
// (see package.json's "build" script). Injects real, crawlable markup for
// the three public marketing routes into dist/, so a bot or link-unfurler
// that never runs JavaScript still sees the actual page — not an empty
// `<div id="root"></div>`.
//
// Deliberately NOT hydration: main.tsx still calls createRoot().render(),
// which replaces this markup with a fresh client render on mount rather than
// reconciling against it. That trades a "hydration mismatch" risk (real, and
// easy to get wrong with media queries and GSAP-driven transforms) for a
// plain, safe pattern: bots and the first paint see real content, then the
// SPA takes over exactly as it always did.
import { readFileSync, writeFileSync, mkdirSync, rmSync } from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const DIST = path.resolve('dist');
const SITE_URL = 'https://13-206-43-42.sslip.io';

const { renderRoute } = await import(
  pathToFileURL(path.resolve('dist-ssr', 'entry-prerender.js')).href
);

const routes = [
  {
    slug: 'home',
    title: "SentinelOps — Know what's wrong before you ship",
    description:
      'Point SentinelOps at a repository. It clones it, runs 33 checks across six categories, and hands back a score out of 100 with exactly what to fix.',
  },
  {
    slug: 'how-it-works',
    title: 'How it works — SentinelOps',
    description:
      'Clone, index, six independent scanners, 33 checks, a weighted score out of 100 — every check returns passed, flagged, skipped, or errored, never a bare pass/fail.',
  },
  {
    slug: 'who-its-for',
    title: "Who it's for — SentinelOps",
    description:
      'For a backend service about to go to production: containerized, serves HTTP, talks to a database. Not for static sites, libraries, or notebooks.',
  },
];

const template = readFileSync(path.join(DIST, 'index.html'), 'utf-8');

function withMeta(html, route) {
  const canonical = `${SITE_URL}/${route.slug}`;
  return html
    .replace(/<title>.*?<\/title>/, `<title>${route.title}</title>`)
    .replace(
      /<meta name="description" content=".*?" \/>/,
      `<meta name="description" content="${route.description}" />`,
    )
    .replace(/<link rel="canonical" href=".*?" \/>/, `<link rel="canonical" href="${canonical}" />`)
    .replace(/<meta property="og:title" content=".*?" \/>/, `<meta property="og:title" content="${route.title}" />`)
    .replace(
      /<meta property="og:description" content=".*?" \/>/,
      `<meta property="og:description" content="${route.description}" />`,
    )
    .replace(/<meta property="og:url" content=".*?" \/>/, `<meta property="og:url" content="${canonical}" />`)
    .replace(/<meta name="twitter:title" content=".*?" \/>/, `<meta name="twitter:title" content="${route.title}" />`)
    .replace(
      /<meta name="twitter:description" content=".*?" \/>/,
      `<meta name="twitter:description" content="${route.description}" />`,
    );
}

for (const route of routes) {
  const appHtml = renderRoute(`/${route.slug}`);
  const html = withMeta(template, route).replace('<div id="root"></div>', `<div id="root">${appHtml}</div>`);
  const outDir = path.join(DIST, route.slug);
  mkdirSync(outDir, { recursive: true });
  writeFileSync(path.join(outDir, 'index.html'), html);
  console.log(`prerendered dist/${route.slug}/index.html`);
}

// Bare `/` renders as a client-side redirect to /home (App.tsx's <Navigate>),
// which a non-JS bot never sees. Ship it with /home's content directly —
// a real browser still redirects on mount exactly as before.
const home = routes[0];
writeFileSync(
  path.join(DIST, 'index.html'),
  withMeta(template, home).replace('<div id="root"></div>', `<div id="root">${renderRoute('/home')}</div>`),
);
console.log('prerendered dist/index.html (home content, client still redirects to /home)');

rmSync(path.resolve('dist-ssr'), { recursive: true, force: true });
