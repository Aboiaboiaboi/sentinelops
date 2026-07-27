/// <reference types="vitest/config" />
import path from 'node:path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },

  // Tests live here rather than in a separate vitest.config.ts so they inherit
  // the `@` alias above instead of redeclaring it.
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/vitest.setup.ts'],
    // Explicit imports from 'vitest' in every test file — no ambient globals.
    globals: false,
    include: ['src/**/*.test.{ts,tsx}'],
  },
  server: {
    port: 5173,
    proxy: {
      // Keeps the browser origin identical for app and API during local dev, so the
      // httpOnly auth cookie is same-origin and CORS never enters the picture.
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
        configure: (proxy) => {
          // Without this, a missing backend surfaces as a bare 500 "Internal
          // Server Error" from the proxy itself, which reads like the API
          // returned it. Say what actually happened instead.
          proxy.on('error', (_err, _req, res) => {
            if (!('writeHead' in res) || res.headersSent) return;
            res.writeHead(503, { 'Content-Type': 'application/json' });
            res.end(
              JSON.stringify({
                detail: 'Backend not reachable at http://localhost:8000.',
              }),
            );
          });
        },
      },
    },
  },
});
