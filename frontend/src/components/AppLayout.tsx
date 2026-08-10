import { Suspense } from 'react';
import { Link, Outlet, useNavigate } from 'react-router-dom';
import { ShieldCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useLogout, useSession } from '@/hooks/useAuth';

export function AppLayout() {
  const navigate = useNavigate();
  const session = useSession();
  const logout = useLogout();

  function handleLogout() {
    // Navigating only after the server has cleared the cookie. Going first
    // would land on the login page with the session still live, and any
    // back-navigation would walk straight back into the app.
    logout.mutate(undefined, {
      onSuccess: () => navigate('/login', { replace: true }),
    });
  }

  return (
    <div className="min-h-screen bg-background">
      <header className="border-b">
        <div className="mx-auto flex h-14 max-w-5xl items-center gap-2 px-4">
          <Link to="/dashboard" className="flex items-center gap-2 font-semibold">
            <ShieldCheck className="size-5 text-primary-bright" />
            SentinelOps
          </Link>

          <div className="ml-auto flex items-center gap-3">
            {logout.isError && (
              <span role="alert" className="text-sm text-destructive">
                Could not sign out.
              </span>
            )}
            {session.data && (
              <span className="hidden text-sm text-muted-foreground sm:inline">
                {session.data.email}
              </span>
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={handleLogout}
              disabled={logout.isPending}
            >
              {logout.isPending ? 'Signing out…' : 'Sign out'}
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-4 py-8">
        {/* Inside the layout, not around it, so the header stays put while a
            lazily-loaded route chunk arrives. See the lazy imports in App.tsx. */}
        <Suspense fallback={<Skeleton className="h-64 w-full" />}>
          <Outlet />
        </Suspense>
      </main>
    </div>
  );
}

/** Centred single-column shell for the login/signup screens. */
export function AuthLayout() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center justify-center gap-2 font-semibold">
          <ShieldCheck className="size-5 text-primary-bright" />
          SentinelOps
        </div>
        <Outlet />
      </div>
    </div>
  );
}
