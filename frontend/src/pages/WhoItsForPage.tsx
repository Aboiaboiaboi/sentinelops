import { Link } from 'react-router-dom';
import { ArrowRight, CheckCircle2, XCircle } from 'lucide-react';
import { Reveal } from '@/components/marketing/Reveal';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';

/** Same content as the README's "What it's for" section — reused, not
 * rewritten. That copy already earned its wording. */
const GOOD_FIT = [
  { thing: 'A product or SaaS API before launch', why: 'Every check applies, and 100 is genuinely reachable' },
  { thing: 'Internal tools and admin dashboards', why: 'Usually the worst offenders, because "it’s only internal"' },
  { thing: 'A codebase you’ve just inherited', why: '31 concrete answers beats a week of reading unfamiliar code' },
  { thing: 'One repo, scanned repeatedly over time', why: 'Watching the score move matters more than any single number' },
];

const POOR_FIT = [
  { thing: 'Static sites, libraries, mobile and CLI apps', why: 'Nothing gets deployed as a service, so most checks don’t apply' },
  { thing: 'Notebooks and research code', why: 'There’s no service here to assess' },
  { thing: 'A monorepo holding several services', why: 'It scores the repo as one unit, so one weak service can hide inside a good average' },
];

export default function WhoItsForPage() {
  return (
    <>
      {/* Hero */}
      <section className="mx-auto max-w-4xl px-4 pb-8 pt-16 text-center sm:pt-24">
        <Reveal className="flex flex-col items-center gap-4">
          <h1 className="max-w-2xl text-balance font-display text-4xl font-semibold tracking-tight sm:text-5xl">
            A backend service you&rsquo;re about to put into production.
          </h1>
          <p className="max-w-xl text-balance text-lg text-muted-foreground">
            Something containerized, that serves HTTP, talks to a database,
            and might eventually run as more than one copy. The question
            every check is really asking is <span className="text-foreground">
            &ldquo;what did we forget?&rdquo;</span> — not the interesting
            problems, the boring fatal ones.
          </p>
        </Reveal>
      </section>

      {/* Good fit */}
      <section className="mx-auto max-w-4xl px-4 py-12 sm:py-16">
        <Reveal className="mb-6">
          <h2 className="font-display text-2xl font-semibold tracking-tight text-primary-bright sm:text-3xl">
            Good fit
          </h2>
        </Reveal>
        <Reveal stagger={40} className="flex flex-col gap-3">
          {GOOD_FIT.map((row) => (
            <Card key={row.thing} className="border-border/60 bg-card/60">
              <CardContent className="flex items-start gap-3 p-4">
                <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-primary-bright" />
                <div>
                  <p className="font-medium text-foreground">{row.thing}</p>
                  <p className="mt-0.5 text-sm text-muted-foreground">{row.why}</p>
                </div>
              </CardContent>
            </Card>
          ))}
        </Reveal>
      </section>

      {/* Poor fit */}
      <section className="border-y bg-secondary/30">
        <div className="mx-auto max-w-4xl px-4 py-12 sm:py-16">
          <Reveal className="mb-6">
            <h2 className="font-display text-2xl font-semibold tracking-tight sm:text-3xl">
              Poor fit
            </h2>
          </Reveal>
          <Reveal stagger={40} className="flex flex-col gap-3">
            {POOR_FIT.map((row) => (
              <Card key={row.thing} className="border-border/60 bg-card/40">
                <CardContent className="flex items-start gap-3 p-4">
                  <XCircle className="mt-0.5 size-5 shrink-0 text-muted-foreground" />
                  <div>
                    <p className="font-medium text-foreground">{row.thing}</p>
                    <p className="mt-0.5 text-sm text-muted-foreground">{row.why}</p>
                  </div>
                </CardContent>
              </Card>
            ))}
          </Reveal>
        </div>
      </section>

      {/* Two honest limits */}
      <section className="mx-auto max-w-3xl px-4 py-12 text-center sm:py-16">
        <Reveal className="flex flex-col gap-4">
          <p className="text-muted-foreground">
            Two honest limits worth knowing. <span className="text-foreground">
            Scores only compare like with like</span> — a CLI tool skips all
            three scalability checks, so it can never reach 90, and its 85
            doesn&rsquo;t mean the same thing as a web service&rsquo;s 85.
            And <span className="text-foreground">it never runs your
            code</span>, so anything that only shows up at runtime is
            invisible to it. Think of it as a readiness checklist that shows
            its work, not a penetration test.
          </p>
        </Reveal>
      </section>

      {/* CTA */}
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
