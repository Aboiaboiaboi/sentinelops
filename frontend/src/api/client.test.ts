import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, isUnauthorized, request } from './client';

/** Builds a Response-alike; jsdom's Response is fine but verbose to configure. */
function respond(
  status: number,
  body: unknown,
  contentType = 'application/json',
): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 500 ? 'Internal Server Error' : '',
    headers: new Headers(contentType ? { 'content-type': contentType } : {}),
    json: async () => body,
    text: async () => (typeof body === 'string' ? body : JSON.stringify(body)),
  } as Response;
}

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

describe('request', () => {
  it('sends credentials on every call', async () => {
    // The whole reason api/ funnels through one function: auth is an httpOnly
    // cookie, and a request missing this option 401s for no visible reason.
    fetchMock.mockResolvedValue(respond(200, { id: 'p1' }));
    await request('/projects');

    const [, init] = fetchMock.mock.calls[0];
    expect(init.credentials).toBe('include');
  });

  it('sends credentials on mutations too', async () => {
    fetchMock.mockResolvedValue(respond(200, {}));
    await request('/projects', { method: 'POST', body: { name: 'x' } });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('/api/projects');
    expect(init.credentials).toBe('include');
    expect(init.method).toBe('POST');
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' });
    expect(init.body).toBe(JSON.stringify({ name: 'x' }));
  });

  it('omits the content-type header when there is no body', async () => {
    fetchMock.mockResolvedValue(respond(200, []));
    await request('/projects');
    expect(fetchMock.mock.calls[0][1].headers).toBeUndefined();
  });

  it('returns null for 204 without touching the body', async () => {
    const json = vi.fn();
    fetchMock.mockResolvedValue({
      ok: true,
      status: 204,
      statusText: '',
      headers: new Headers(),
      json,
      text: async () => '',
    } as unknown as Response);

    await expect(request('/projects/p1')).resolves.toBeNull();
    expect(json).not.toHaveBeenCalled();
  });

  it('surfaces a FastAPI string detail as the error message', async () => {
    fetchMock.mockResolvedValue(respond(400, { detail: 'Repository URL is invalid' }));
    await expect(request('/projects')).rejects.toThrow('Repository URL is invalid');
  });

  it('unwraps the first message from a 422 validation list', async () => {
    fetchMock.mockResolvedValue(
      respond(422, { detail: [{ msg: 'value is not a valid email address' }] }),
    );
    await expect(request('/auth/signup')).rejects.toThrow(
      'value is not a valid email address',
    );
  });

  it('falls back to statusText when the body explains nothing', async () => {
    fetchMock.mockResolvedValue(respond(500, null));
    await expect(request('/projects')).rejects.toThrow('Internal Server Error');
  });

  it('carries the status code so callers can branch on 401', async () => {
    fetchMock.mockResolvedValue(respond(401, { detail: 'Not authenticated' }));

    const error = await request('/projects').catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(401);
    expect(isUnauthorized(error)).toBe(true);
  });

  it('reports an unreachable backend as status 0, not a bare "Failed to fetch"', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));

    const error = await request('/projects').catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(0);
    expect((error as ApiError).message).toMatch(/Is the backend running/);
  });

  it('lets an abort propagate untouched so TanStack Query can see it', async () => {
    fetchMock.mockRejectedValue(new DOMException('aborted', 'AbortError'));
    await expect(request('/projects')).rejects.toBeInstanceOf(DOMException);
  });
});

describe('isUnauthorized', () => {
  it('is false for other API failures and for non-API errors', () => {
    expect(isUnauthorized(new ApiError(403, 'Forbidden'))).toBe(false);
    expect(isUnauthorized(new Error('boom'))).toBe(false);
    expect(isUnauthorized(undefined)).toBe(false);
  });
});
