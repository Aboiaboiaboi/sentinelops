import { Link } from 'react-router-dom';
import { ArrowRight, GitBranch, ListChecks, Cog, ShieldOff, Gauge } from 'lucide-react';
import { ChecksExplorer } from '@/components/marketing/ChecksExplorer';
import { Eyebrow } from '@/components/Eyebrow';
import { PullQuote } from '@/components/marketing/PullQuote';
import { Reveal } from '@/components/marketing/Reveal';
import { SectionHeading } from '@/components/marketing/SectionHeading';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { CHECK_CATALOG, TOTAL_CHECK_COUNT } from '@/lib/checkCatalog';

const PIPELINE = [
  { icon: GitBranch, title: 'Clone', description: 'A shallow, read-only clone — no history, no submodules.' },
  { icon: Cog, title: 'Index', description: 'The tree is walked once into a shared repository index.' },
  {
    icon: ListChecks,
    title: 'Six scanners',
    description:
      'Security, Reliability, Deployment, Architecture, Scalability, Observability — each runs independently.',
  },
  {
    icon: Gauge,
    title: `${TOTAL_CHECK_COUNT} checks`,
    description: 'Every check returns passed, flagged, skipped, or errored — never a bare pass/fail.',
  },
  { icon: ShieldOff, title: 'Score', description: 'Weighted into a score out of 100, with every finding attached.' },
];

const SANDBOX_POINTS = [
  'No network access at all',
  'Read-only root filesystem',
  'Every extra Linux capability dropped',
  'Runs as a non-root user',
  'Hard memory and CPU caps',
  'A tool that fails to run is errored, never passed',
];

export default function HowItWorksPage() {
  return (
    <>
      {/* Hero */}
      <section className="mx-auto max-w-6xl px-4 pb-12 pt-16 sm:pt-24">
        <Reveal stagger={70} className="flex max-w-3xl flex-col items-start gap-5">
          <Eyebrow>How it works</Eyebrow>
          <h1 className="text-balance font-display font-semibold leading-[1.05] tracking-[-0.03em] text-[clamp(2.5rem,6vw,4rem)]">
            What actually happens when you press scan.
          </h1>
          <p className="max-w-xl text-lg text-muted-foreground">
            The API answers in milliseconds — it only creates a record and
            queues a job. A background worker does the slow part.
          </p>
        </Reveal>
      </section>

      {/* Pipeline — a numbered editorial figure. */}
      <section className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
        <Reveal className="mb-10">
          <SectionHeading number="01" eyebrow="The pipeline">
            Five steps, start to finish.
          </SectionHeading>
        </Reveal>
        <Reveal stagger={80} className="flex flex-col gap-px overflow-hidden rounded-lg border border-border/60">
          {PIPELINE.map((step, i) => (
            <div
              key={step.title}
              className="flex items-start gap-5 bg-card/40 p-5 sm:items-center sm:gap-8 sm:p-6"
            >
              <span
                className="font-mono text-sm font-medium text-editorial"
                aria-hidden="true"
              >
                {String(i + 1).padStart(2, '0')}
              </span>
              <step.icon className="mt-0.5 size-5 shrink-0 text-primary-bright sm:mt-0" aria-hidden="true" />
              <div className="flex flex-col gap-1 sm:flex-row sm:items-baseline sm:gap-4">
                <h3 className="font-display text-base font-semibold sm:w-32 sm:shrink-0">
                  {step.title}
                </h3>
                <p className="text-sm text-muted-foreground">{step.description}</p>
              </div>
            </div>
          ))}
        </Reveal>
      </section>

      {/* Category weights */}
      <section className="border-y bg-secondary/20">
        <div className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
          <Reveal className="mb-10">
            <SectionHeading
              number="02"
              eyebrow="The rubric"
              lede={
                <>
                  A category that couldn&rsquo;t be assessed contributes nothing
                  — it&rsquo;s never quietly excluded from the total.
                </>
              }
            >
              Six categories, weighted to sum to 100.
            </SectionHeading>
          </Reveal>
          <Reveal stagger={40} className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            {CHECK_CATALOG.map((cat) => (
              <Card key={cat.category} className="border-border/60 bg-card/60">
                <CardContent className="flex items-baseline justify-between p-4">
                  <p className="font-display text-sm font-medium">{cat.category}</p>
                  <p className="font-mono text-2xl text-editorial">{cat.weight}</p>
                </CardContent>
              </Card>
            ))}
          </Reveal>
        </div>
      </section>

      {/* The checks themselves */}
      <section className="mx-auto max-w-6xl px-4 py-16 sm:py-20">
        <Reveal className="mb-10">
          <SectionHeading
            number="03"
            eyebrow="The checks"
            lede="Grouped by category. Expand any of them."
          >
            All {TOTAL_CHECK_COUNT} checks, if you want to see them.
          </SectionHeading>
        </Reveal>
        <ChecksExplorer />
      </section>

      {/* Sandboxing */}
      <section className="border-y bg-secondary/20">
        <div className="mx-auto max-w-5xl px-4 py-16 sm:py-20">
          <Reveal className="mb-10">
            <SectionHeading number="04" eyebrow="Isolation">
              The one part that runs a stranger&rsquo;s code is treated as
              hostile.
            </SectionHeading>
          </Reveal>
          <div className="grid gap-10 lg:grid-cols-2 lg:gap-16">
            <Reveal stagger={40} className="flex flex-col gap-3">
              {SANDBOX_POINTS.map((point) => (
                <div
                  key={point}
                  className="flex items-start gap-3 rounded-lg border border-border/60 bg-card/40 p-4"
                >
                  <ShieldOff className="mt-0.5 size-4 shrink-0 text-primary-bright" aria-hidden="true" />
                  <p className="text-sm text-foreground">{point}</p>
                </div>
              ))}
            </Reveal>
            <Reveal className="lg:self-center">
              <PullQuote cite="The rule, everywhere in the codebase">
                A check that couldn&rsquo;t run is errored, never passed. A false
                all-clear is the one result worse than no result.
              </PullQuote>
            </Reveal>
          </div>
        </div>
      </section>

      {/* Closing CTA */}
      <section className="mx-auto max-w-6xl px-4 py-20 sm:py-28">
        <Reveal className="flex max-w-2xl flex-col items-start gap-6">
          <h2 className="text-balance font-display font-semibold leading-[1.05] tracking-[-0.02em] text-[clamp(2rem,5vw,3.25rem)]">
            See it run against your own repository.
          </h2>
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
