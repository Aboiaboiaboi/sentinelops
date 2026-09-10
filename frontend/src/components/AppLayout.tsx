import { Suspense } from 'react';
import { Link, Outlet, useNavigate } from 'react-router-dom';
import { Logo } from '@/components/Logo';
import { Eyebrow } from '@/components/Eyebrow';
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
    <div className="flex min-h-screen flex-col bg-background">
      <header className="border-b border-border/60 print:hidden">
        <div className="mx-auto flex h-14 max-w-5xl items-center gap-6 px-4">
          {/* Home, not /dashboard: the logo goes to the front door everywhere
              in the app, matching the marketing header. The "Projects" link
              below is what now carries you back into the app. */}
          <Link to="/home" className="rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background">
            <Logo size="sm" />
          </Link>

          <Link
            to="/dashboard"
            className="font-mono text-xs uppercase tracking-[0.15em] text-muted-foreground transition-colors hover:text-foreground"
          >
            Projects
          </Link>

          <div className="ml-auto flex items-center gap-3">
            {logout.isError && (
              <span role="alert" className="text-sm text-destructive">
                Could not sign out.
              </span>
            )}
            {session.data && (
              <span className="hidden font-mono text-xs text-muted-foreground sm:inline">
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

      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-8 print:py-0">
        {/* Inside the layout, not around it, so the header stays put while a
            lazily-loaded route chunk arrives. See the lazy imports in App.tsx. */}
        <Suspense fallback={<Skeleton className="h-64 w-full" />}>
          <Outlet />
        </Suspense>
      </main>

      <footer className="border-t border-border/60 print:hidden">
        <div className="mx-auto max-w-5xl px-4 py-6">
          <p className="text-xs text-muted-foreground">
            It reads code. It never runs the repository, deploys anything, or
            changes it.
          </p>
        </div>
      </footer>
    </div>
  );
}

/** Centred single-column shell for the login/signup screens. */
export function AuthLayout() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-background px-4 py-12">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center gap-2">
          <Link
            to="/home"
            className="rounded-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
          >
            <Logo size="md" />
          </Link>
          <Eyebrow className="text-muted-foreground">Production-readiness scanner</Eyebrow>
        </div>
        <Outlet />
        <p className="mt-6 text-center text-xs text-muted-foreground">
          <Link to="/how-it-works" className="transition-colors hover:text-foreground">
            How it works
          </Link>
        </p>
      </div>
    </div>
  );
}
