import { useEffect } from 'react';
import { Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { subscribeToUnauthorized } from '@/lib/queryClient';
import { safeRedirect } from '@/lib/redirect';

/**
 * Route guard for cookie-based auth.
 *
 * With an httpOnly cookie there is nothing readable to check up front, and the
 * API has no /auth/me — so this cannot pre-verify a session. Instead it
 * renders optimistically and reacts when any request comes back 401, which the
 * query client broadcasts. See lib/queryClient.ts.
 *
 * Consequence worth knowing: a logged-out user briefly sees the page skeleton
 * before being bounced. A session endpoint would remove that flash.
 */
export function ProtectedRoute() {
  const navigate = useNavigate();
  const location = useLocation();

  useEffect(
    () =>
      subscribeToUnauthorized(() => {
        // `replace` keeps the dead protected URL out of history, and `from`
        // lets the login page send the user back where they were headed.
        //
        // Validated on the way in as well as on the way out. The login page is
        // the only consumer today and it validates too, but a value that is
        // never stored unsafely cannot be read unsafely by whatever consumes it
        // next — and this is the cheaper of the two places to be sure.
        navigate('/login', {
          replace: true,
          state: { from: safeRedirect(location.pathname) },
        });
      }),
    [navigate, location.pathname],
  );

  return <Outlet />;
}

/** Sends `/` to the dashboard. */
export function IndexRedirect() {
  return <Navigate to="/dashboard" replace />;
}
