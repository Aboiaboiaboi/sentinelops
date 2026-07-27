import { Suspense } from 'react';
import { Link, Outlet } from 'react-router-dom';
import { ShieldCheck } from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';

export function AppLayout() {
  return (
    <div className="min-h-screen bg-background">
      <header className="border-b">
        <div className="mx-auto flex h-14 max-w-5xl items-center gap-2 px-4">
          <Link to="/dashboard" className="flex items-center gap-2 font-semibold">
            <ShieldCheck className="size-5 text-primary-bright" />
            SentinelOps
          </Link>
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
