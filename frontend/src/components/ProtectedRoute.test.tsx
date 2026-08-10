import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { ProtectedRoute } from './ProtectedRoute';
import type { User } from '@/types/project';

type SessionResult = {
  data?: User | null;
  isPending: boolean;
  isError?: boolean;
};

let session: SessionResult = { data: null, isPending: false };

vi.mock('@/hooks/useAuth', () => ({
  useSession: () => session,
}));

const USER: User = {
  id: 'user_1',
  email: 'engineer@example.com',
  created_at: '2026-01-01T00:00:00Z',
};

function renderAt(result: SessionResult, path = '/dashboard') {
  session = result;
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route element={<ProtectedRoute />}>
          <Route path="/dashboard" element={<p>dashboard</p>} />
        </Route>
        <Route path="/login" element={<p>login</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('ProtectedRoute', () => {
  it('renders the route once a session is confirmed', () => {
    renderAt({ data: USER, isPending: false });

    expect(screen.getByText('dashboard')).toBeInTheDocument();
  });

  it('shows neither the route nor the login page while the session is unknown', () => {
    // The flash /auth/me exists to remove: rendering the protected page and
    // then replacing it with login is what a returning visitor used to see.
    renderAt({ isPending: true });

    expect(screen.queryByText('dashboard')).not.toBeInTheDocument();
    expect(screen.queryByText('login')).not.toBeInTheDocument();
  });

  it('redirects to login when there is no session', () => {
    renderAt({ data: null, isPending: false });

    expect(screen.getByText('login')).toBeInTheDocument();
  });

  it('does not sign the user out when the API is unreachable', () => {
    // `undefined` with isError is a failed request, not a rejected cookie.
    // Bouncing to a login page that also cannot reach the API would replace a
    // legible error with a confusing one.
    renderAt({ data: undefined, isPending: false, isError: true });

    expect(screen.getByText('dashboard')).toBeInTheDocument();
  });
});
