import { renderToString } from 'react-dom/server';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Route, Routes } from 'react-router-dom';
import { StaticRouter } from 'react-router-dom';
import { MarketingLayout } from '@/components/marketing/MarketingLayout';
import { sessionKey } from '@/hooks/useAuth';
import LandingPage from '@/pages/LandingPage';
import HowItWorksPage from '@/pages/HowItWorksPage';
import WhoItsForPage from '@/pages/WhoItsForPage';

/**
 * Node-side entry for `scripts/prerender.mjs`, built separately via
 * `vite build --ssr` — never imported by the browser bundle. Renders only the
 * three public marketing routes (App.tsx's own lazy() imports for these pages
 * would suspend forever under plain renderToString, which doesn't wait on
 * promises; direct imports here sidestep that entirely).
 *
 * The session query is seeded to `null` and retries are off, so this never
 * attempts a real fetch — a prerendered page always reflects the logged-out
 * view. The client re-fetches and re-renders on mount regardless, so a
 * visitor who is actually signed in sees the right state within a frame.
 */
export function renderRoute(pathname: string): string {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  queryClient.setQueryData(sessionKey, null);

  return renderToString(
    <QueryClientProvider client={queryClient}>
      <StaticRouter location={pathname}>
        <Routes>
          <Route element={<MarketingLayout />}>
            <Route path="/home" element={<LandingPage />} />
            <Route path="/how-it-works" element={<HowItWorksPage />} />
            <Route path="/who-its-for" element={<WhoItsForPage />} />
          </Route>
        </Routes>
      </StaticRouter>
    </QueryClientProvider>,
  );
}
