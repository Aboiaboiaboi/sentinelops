import { useMutation, useQueryClient } from '@tanstack/react-query';
import { login, signup } from '@/api/auth';
import type { Credentials } from '@/types/project';

/**
 * There is no logout endpoint and no session endpoint in the API surface.
 * Login/signup are therefore the only auth calls the frontend makes; "logged
 * out" is discovered reactively when a request comes back 401 (see
 * lib/queryClient.ts).
 */
export function useLogin() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (credentials: Credentials) => login(credentials),
    // Anything cached belonged to the previous session.
    onSuccess: () => client.clear(),
  });
}

export function useSignup() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (credentials: Credentials) => signup(credentials),
    onSuccess: () => client.clear(),
  });
}
