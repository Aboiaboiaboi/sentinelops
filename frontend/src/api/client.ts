/**
 * The one HTTP entry point for the whole app.
 *
 * Everything in api/ goes through `request()`. That is deliberate: auth rides on
 * an httpOnly cookie, so every single call needs `credentials: 'include'` or the
 * cookie is silently omitted and the request 401s for no visible reason. Setting
 * it per-call is a footgun — one missed option and you get a confusing bug. It
 * lives here once instead.
 *
 * No React imports in this file or its siblings — api/ stays independently
 * testable, and hooks/ is the only layer that knows about TanStack Query.
 */

const BASE_URL = import.meta.env.VITE_API_URL ?? '/api';

/** Thrown on any non-2xx response. Carries `status` so callers can branch on 401. */
export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

/** True when the failure means "not authenticated" rather than a real error. */
export function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  body?: unknown;
  signal?: AbortSignal;
}

async function parseBody(response: Response): Promise<unknown> {
  if (response.status === 204) return null;
  const contentType = response.headers.get('content-type') ?? '';
  if (!contentType.includes('application/json')) {
    const text = await response.text();
    return text.length > 0 ? text : null;
  }
  try {
    return await response.json();
  } catch {
    return null;
  }
}

/** FastAPI returns `{ detail: ... }`; fall back to the status text otherwise. */
function extractMessage(status: number, body: unknown, statusText: string): string {
  if (typeof body === 'string' && body.length > 0) return body;
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === 'string') return detail;
    // 422 validation errors arrive as a list of objects.
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { msg?: string };
      if (typeof first?.msg === 'string') return first.msg;
    }
  }
  return statusText || `Request failed with status ${status}`;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal } = options;

  let response: Response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      method,
      // Sends and accepts the httpOnly JWT cookie. Non-negotiable.
      credentials: 'include',
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (cause) {
    // fetch() only rejects on network-level failure. With no backend running
    // that is the common case, so give it a legible message rather than
    // letting a bare "Failed to fetch" reach the UI.
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause;
    throw new ApiError(0, 'Could not reach the API. Is the backend running?', cause);
  }

  const parsed = await parseBody(response);

  if (!response.ok) {
    throw new ApiError(
      response.status,
      extractMessage(response.status, parsed, response.statusText),
      parsed,
    );
  }

  return parsed as T;
}
