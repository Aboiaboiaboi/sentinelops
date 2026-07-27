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
