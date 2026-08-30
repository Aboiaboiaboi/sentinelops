import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { login, logout, me, signup } from '@/api/auth';
import { isUnauthorized } from '@/api/client';
import type { Credentials, User } from '@/types/project';

/**
 * The one cache key holding "who is signed in". Login and signup seed it with
 * the user they already received, so the app never asks /auth/me for something
 * it was just told.
 */
export const sessionKey = ['session'] as const;

/**
 * The signed-in user, or `null` when the cookie is missing or dead.
 *
 * The cookie is httpOnly, so this request is the only way to answer the
 * question after a page refresh. A 401 is resolved to `null` rather than
 * thrown: it is this query's ordinary negative answer, and letting it surface
 * as an error would put it through the global 401 handler in lib/queryClient.ts
 * — which redirects to /login. That is right for every *other* request in the
 * app and wrong here, where "not signed in" is exactly what was being asked.
 */
export function useSession() {
  return useQuery<User | null>({
    queryKey: sessionKey,
    queryFn: async () => {
      try {
        return await me();
      } catch (error) {
        if (isUnauthorized(error)) return null;
        throw error;
      }
    },
    // A session does not change under the app's feet, and this runs on every
    // protected route. Refetching it on an interval would add a request per
    // page for an answer that only changes when this app itself changes it.
    staleTime: Infinity,
  });
}

export function useLogin() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (credentials: Credentials) => login(credentials),
    // Anything cached belonged to the previous session.
    onSuccess: (user) => {
      client.clear();
      client.setQueryData(sessionKey, user);
    },
  });
}

export function useSignup() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (credentials: Credentials) => signup(credentials),
    onSuccess: (user) => {
      client.clear();
      client.setQueryData(sessionKey, user);
    },
  });
}

/**
 * Ends the session. The cookie is httpOnly, so only the server can clear it —
 * dropping the cached user client-side would leave a browser that is still
 * authenticated to every endpoint.
 */
export function useLogout() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => logout(),
    // Cleared after the server has answered, not before: a failed logout must
    // leave the app in the signed-in state it is actually still in.
    onSuccess: () => {
      client.clear();
      client.setQueryData(sessionKey, null);
    },
  });
}
