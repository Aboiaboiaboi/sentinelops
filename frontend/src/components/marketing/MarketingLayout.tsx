import { Suspense, useEffect, useState } from 'react';
import { Link, Outlet } from 'react-router-dom';
import { Github } from 'lucide-react';
import { Logo } from '@/components/Logo';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useSession } from '@/hooks/useAuth';

/**
 * Shell for the public pages (/home, /how-it-works, /who-its-for). Unlike AppLayout, nothing
 * here requires a session — useSession() safely resolves a 401 to `null`
 * (see hooks/useAuth.ts), so a logged-out visitor just sees the logged-out
 * CTA instead of being redirected anywhere.
 */
export function MarketingLayout() {
  const session = useSession();
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    function onScroll() {
      setScrolled(window.scrollY > 8);
    }
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  return (
    <div className="min-h-screen bg-background">
      <header
        className={
          'sticky top-0 z-40 border-b backdrop-blur transition-colors ' +
          (scrolled ? 'border-border bg-background/80' : 'border-transparent bg-transparent')
        }
      >
        <div className="mx-auto flex h-16 max-w-6xl items-center gap-6 px-4">
          <Link to="/home">
            <Logo size="md" />
          </Link>

          <nav className="ml-auto flex items-center gap-6">
            <Link
              to="/how-it-works"
              className="hidden font-mono text-xs uppercase tracking-[0.15em] text-muted-foreground transition-colors hover:text-foreground sm:inline"
            >
              How it works
            </Link>
            <Link
              to="/who-its-for"
              className="hidden font-mono text-xs uppercase tracking-[0.15em] text-muted-foreground transition-colors hover:text-foreground sm:inline"
            >
              Who it&rsquo;s for
            </Link>
            {session.data ? (
              <Button asChild size="sm">
                <Link to="/dashboard">Go to dashboard</Link>
              </Button>
            ) : (
              <>
                <Link
                  to="/login"
                  className="hidden font-mono text-xs uppercase tracking-[0.15em] text-muted-foreground transition-colors hover:text-foreground sm:inline"
                >
                  Sign in
                </Link>
                <Button asChild size="sm">
                  <Link to="/signup">Get started</Link>
                </Button>
              </>
            )}
          </nav>
        </div>
      </header>

      <main>
        <Suspense fallback={<div className="mx-auto max-w-6xl px-4 py-24"><Skeleton className="h-96 w-full" /></div>}>
          <Outlet />
        </Suspense>
      </main>

      <footer className="border-t">
        <div className="mx-auto flex max-w-6xl flex-col items-center gap-4 px-4 py-8 text-sm text-muted-foreground">
          {/* Always visible, unlike the header's nav links (hidden below
              sm) — with three public pages now, a phone visitor needs some
              way to reach them besides the header CTA. */}
          <nav className="flex flex-wrap items-center justify-center gap-x-6 gap-y-2 font-mono text-xs uppercase tracking-[0.15em]">
            <Link to="/home" className="transition-colors hover:text-foreground">
              Home
            </Link>
            <Link to="/how-it-works" className="transition-colors hover:text-foreground">
              How it works
            </Link>
            <Link to="/who-its-for" className="transition-colors hover:text-foreground">
              Who it&rsquo;s for
            </Link>
          </nav>
          <div className="flex flex-col items-center gap-3 sm:flex-row sm:justify-between sm:self-stretch">
            <p>It reads code. It never runs the repository, deploys anything, or changes it.</p>
            <a
              href="https://github.com/Aboiaboiaboi/sentinelops"
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1.5 transition-colors hover:text-foreground"
            >
              <Github className="size-4" />
              View on GitHub
            </a>
          </div>
        </div>
      </footer>
    </div>
  );
}
