import { MutationCache, QueryCache, QueryClient } from '@tanstack/react-query';
import { ApiError, isUnauthorized } from '@/api/client';

/**
 * Central 401 handling.
 *
 * The JWT lives in an httpOnly cookie, so the frontend cannot read it and has no
 * local way to answer "am I logged in?". The documented API surface has no
 * /auth/me either. So authentication state is inferred from the API's answers:
 * any 401 from any query or mutation means the session is gone.
 *
 * Subscribers are notified rather than redirecting with window.location, so the
 * router can navigate without a full page reload (which would drop the query
 * cache and flash the whole app).
 */
type UnauthorizedListener = () => void;

const listeners = new Set<UnauthorizedListener>();

export function subscribeToUnauthorized(listener: UnauthorizedListener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function notifyUnauthorized(error: unknown): void {
  if (!isUnauthorized(error)) return;
  for (const listener of listeners) listener();
}

export const queryClient = new QueryClient({
  queryCache: new QueryCache({ onError: notifyUnauthorized }),
  mutationCache: new MutationCache({ onError: notifyUnauthorized }),
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        // Retrying a 401 or a 404 just delays the inevitable.
        if (error instanceof ApiError && error.status >= 400 && error.status < 500) {
          return false;
        }
        return failureCount < 2;
      },
    },
  },
});
