import { request } from './client';
import type { Credentials, User } from '@/types/project';

/**
 * Login returns the user; the JWT itself arrives as a Set-Cookie header the
 * browser stores and we never see. There is nothing to persist client-side.
 */
export function login(credentials: Credentials): Promise<User> {
  return request<User>('/auth/login', { method: 'POST', body: credentials });
}

export function signup(credentials: Credentials): Promise<User> {
  return request<User>('/auth/signup', { method: 'POST', body: credentials });
}

/**
 * Who the cookie belongs to. 401 when there is no session — which is an
 * ordinary answer here, not an error, and the caller in hooks/useAuth.ts treats
 * it as one.
 */
export function me(): Promise<User> {
  return request<User>('/auth/me');
}

/** Expires the cookie server-side. 204, no body. */
export function logout(): Promise<null> {
  return request<null>('/auth/logout', { method: 'POST' });
}
