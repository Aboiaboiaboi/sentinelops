import { describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/api/client';
import { queryClient, subscribeToUnauthorized } from './queryClient';

/**
 * The 401 broadcast is the app's entire notion of "logged out" — ProtectedRoute
 * has nothing else to go on, since the JWT cookie is unreadable and there is no
 * /auth/me. If this stops firing, a dead session silently looks like a live one.
 */
describe('subscribeToUnauthorized', () => {
  function emit(error: Error) {
    // Reach the same handler the QueryCache/MutationCache are wired to.
    queryClient.getQueryCache().config.onError?.(error, {} as never);
  }

  it('notifies every subscriber on a 401', () => {
    const first = vi.fn();
    const second = vi.fn();
    const unsubFirst = subscribeToUnauthorized(first);
    const unsubSecond = subscribeToUnauthorized(second);

    emit(new ApiError(401, 'Not authenticated'));

    expect(first).toHaveBeenCalledOnce();
    expect(second).toHaveBeenCalledOnce();

    unsubFirst();
    unsubSecond();
  });

  it('ignores failures that are not 401s', () => {
    const listener = vi.fn();
    const unsub = subscribeToUnauthorized(listener);

    emit(new ApiError(500, 'Internal Server Error'));
    emit(new ApiError(0, 'Could not reach the API.'));
    emit(new Error('boom'));

    expect(listener).not.toHaveBeenCalled();
    unsub();
  });

  it('stops delivering after unsubscribe', () => {
    const listener = vi.fn();
    subscribeToUnauthorized(listener)();

    emit(new ApiError(401, 'Not authenticated'));

    expect(listener).not.toHaveBeenCalled();
  });
});

describe('query defaults', () => {
  const { retry } = queryClient.getDefaultOptions().queries ?? {};

  it('does not retry 4xx responses', () => {
    expect(typeof retry).toBe('function');
    const shouldRetry = retry as (n: number, e: Error) => boolean;
    expect(shouldRetry(0, new ApiError(401, 'Not authenticated'))).toBe(false);
    expect(shouldRetry(0, new ApiError(404, 'Not Found'))).toBe(false);
  });

  it('retries server and network errors, but gives up after two attempts', () => {
    const shouldRetry = retry as (n: number, e: Error) => boolean;
    expect(shouldRetry(0, new ApiError(503, 'Service Unavailable'))).toBe(true);
    expect(shouldRetry(1, new ApiError(0, 'Could not reach the API.'))).toBe(true);
    expect(shouldRetry(2, new ApiError(503, 'Service Unavailable'))).toBe(false);
  });
});
