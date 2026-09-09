import { Link } from 'react-router-dom';
import { ArrowRight, GitBranch, ListChecks, Cog, ShieldOff, Gauge } from 'lucide-react';
import { Reveal } from '@/components/marketing/Reveal';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';

const PIPELINE = [
  { icon: GitBranch, title: 'Clone', description: 'A shallow, read-only clone — no history, no submodules.' },
  { icon: Cog, title: 'Index', description: 'The tree is walked once into a shared repository index.' },
  { icon: ListChecks, title: '6 scanners', description: 'Security, Reliability, Architecture, Deployment, Observability, Scalability — each runs independently.' },
  { icon: Gauge, title: '31 checks', description: 'Every check returns passed, failed, skipped, or errored — never a bare pass/fail.' },
  { icon: ShieldOff, title: 'Score', description: 'Weighted into a score out of 100, with every finding attached.' },
];

const CATEGORIES = [
  { name: 'Security', weight: 25 },
  { name: 'Reliability', weight: 20 },
  { name: 'Architecture', weight: 20 },
  { name: 'Deployment', weight: 15 },
  { name: 'Observability', weight: 10 },
  { name: 'Scalability', weight: 10 },
];

export default function HowItWorksPage() {
  return (
    <>
      <section className="mx-auto max-w-6xl px-4 pb-8 pt-16 text-center sm:pt-24">
        <Reveal className="flex flex-col items-center gap-4">
          <h1 className="max-w-2xl text-balance font-display text-4xl font-semibold tracking-tight sm:text-5xl">
            What actually happens when you press scan.
          </h1>
          <p className="max-w-xl text-balance text-lg text-muted-foreground">
            The API answers in milliseconds — it only creates a record and
            queues a job. A background worker does the slow part.
          </p>
        </Reveal>
      </section>

      {/* Pipeline */}
      <section className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
        <Reveal stagger={80} className="relative flex flex-col gap-8 sm:flex-row sm:items-start sm:gap-4">
          {PIPELINE.map((step, i) => (
            <div key={step.title} className="relative flex flex-1 flex-col items-center gap-3 text-center">
              {i < PIPELINE.length - 1 && (
                <div className="absolute left-1/2 top-6 hidden h-px w-full origin-left bg-border sm:block" />
              )}
              <div className="relative z-10 flex size-12 items-center justify-center rounded-full border border-border bg-card">
                <step.icon className="size-5 text-primary-bright" />
              </div>
              <h3 className="font-display text-base font-semibold">{step.title}</h3>
              <p className="text-sm text-muted-foreground">{step.description}</p>
            </div>
          ))}
        </Reveal>
      </section>

      {/* Category weights */}
      <section className="border-y bg-secondary/30">
        <div className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
          <Reveal className="mb-10 text-center">
            <h2 className="font-display text-2xl font-semibold tracking-tight sm:text-3xl">
              Six categories, weighted to sum to 100.
            </h2>
            <p className="mt-2 text-muted-foreground">
              A category that couldn&rsquo;t be assessed contributes nothing
              — it&rsquo;s never quietly excluded from the total.
            </p>
          </Reveal>
          <Reveal stagger={40} className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            {CATEGORIES.map((cat) => (
              <Card key={cat.name} className="border-border/60 bg-card/60">
                <CardContent className="p-4">
                  <p className="font-display text-sm font-medium">{cat.name}</p>
                  <p className="font-mono text-2xl text-primary-bright">{cat.weight}</p>
                </CardContent>
              </Card>
            ))}
          </Reveal>
        </div>
      </section>

      {/* Sandboxing */}
      <section className="mx-auto max-w-4xl px-4 py-16 sm:py-20">
        <Reveal className="text-center">
          <h2 className="font-display text-2xl font-semibold tracking-tight sm:text-3xl">
            The one part that runs a stranger&rsquo;s code is treated as
            hostile.
          </h2>
        </Reveal>
        <Reveal stagger={40} className="mt-8 grid gap-4 sm:grid-cols-2">
          {[
            'No network access at all',
            'Read-only root filesystem',
            'Every extra Linux capability dropped',
            'Runs as a non-root user',
            'Hard memory and CPU caps',
            'A tool that fails to run is errored, never passed',
          ].map((point) => (
            <div key={point} className="flex items-start gap-3 rounded-lg border border-border/60 bg-card/40 p-4">
              <ShieldOff className="mt-0.5 size-4 shrink-0 text-primary-bright" />
              <p className="text-sm text-foreground">{point}</p>
            </div>
          ))}
        </Reveal>
      </section>

      {/* Closing CTA */}
      <section className="mx-auto max-w-6xl px-4 py-16 text-center sm:py-24">
        <Reveal className="flex flex-col items-center gap-6">
          <Button asChild size="lg">
            <Link to="/signup">
              Scan a repository
              <ArrowRight className="size-4" />
            </Link>
          </Button>
        </Reveal>
      </section>
    </>
  );
}
