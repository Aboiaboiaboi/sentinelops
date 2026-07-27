import { Component, type ErrorInfo, type ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

/**
 * Last-resort catch for render-time exceptions, which React otherwise handles by
 * unmounting the whole tree — leaving a blank page with the reason only in the
 * console.
 *
 * A class is not a stylistic choice here: componentDidCatch has no hook
 * equivalent. React Router's `errorElement` is also unavailable, since the app
 * uses <BrowserRouter> + <Routes> rather than a data router.
 *
 * Note this does NOT catch async failures — a rejected fetch never passes
 * through render. Those are already handled per-query by TanStack Query and
 * surfaced as Alerts by each page.
 */
interface Props {
  children: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Nowhere to report to yet. Keep the component stack, which is the part
    // React strips from the bare error, so the console still shows where it came
    // from once this boundary has swallowed it.
    console.error('Unhandled render error:', error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div className="flex min-h-screen items-center justify-center px-4">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle>Something went wrong</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">
              The page failed to render. Reloading usually clears it.
            </p>
            {/* The message, not the stack: enough to report the problem without
                putting internals on screen. */}
            <p className="rounded-md border bg-muted/40 p-3 font-mono text-xs break-words">
              {error.message}
            </p>
            <Button onClick={() => window.location.reload()}>Reload the page</Button>
          </CardContent>
        </Card>
      </div>
    );
  }
}
