import { useEffect } from 'react';
import { Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { Skeleton } from '@/components/ui/skeleton';
import { useSession } from '@/hooks/useAuth';
import { subscribeToUnauthorized } from '@/lib/queryClient';
import { safeRedirect } from '@/lib/redirect';

/**
 * Route guard for cookie-based auth.
 *
 * Two mechanisms, and both are needed. `useSession` answers "is there a session
 * *now*" by asking /auth/me, which is the only way to know before rendering —
 * the cookie is httpOnly and unreadable from here. The 401 subscription then
 * catches a session that dies *while* the app is open, which no up-front check
 * can see.
 *
 * Before /auth/me existed this rendered optimistically and waited to be told
 * 401 by some other request, so a returning visitor with a dead cookie saw the
 * page skeleton before being bounced.
 */
export function ProtectedRoute() {
  const navigate = useNavigate();
  const location = useLocation();
  const session = useSession();

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

  // The session request is one round trip on a cold load and cached from then
  // on. Rendering the route underneath in the meantime would show a skeleton
  // that might be replaced by the login page a moment later, which is the flash
  // this endpoint was added to remove.
  if (session.isPending) {
    // This sits *above* AppLayout in the route tree, so there is no header to
    // fill in behind — the placeholder has to supply its own page shell.
    return (
      <div className="min-h-screen bg-background">
        <div className="mx-auto max-w-5xl px-4 py-8">
          <Skeleton className="h-64 w-full" />
        </div>
      </div>
    );
  }

  // Only `null` means "no session". A network failure leaves `data` undefined
  // and `isError` set — that is the API being unreachable, not the user being
  // signed out, and bouncing to a login page that also cannot reach the API
  // would replace a legible error with a confusing one.
  if (session.data === null) {
    return <Navigate to="/login" replace state={{ from: safeRedirect(location.pathname) }} />;
  }

  return <Outlet />;
}

/** Sends `/` to the dashboard. */
export function IndexRedirect() {
  return <Navigate to="/dashboard" replace />;
}
