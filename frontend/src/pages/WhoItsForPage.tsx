import { Link } from 'react-router-dom';
import { ArrowRight, Check, Minus } from 'lucide-react';
import { Eyebrow } from '@/components/Eyebrow';
import { Reveal } from '@/components/marketing/Reveal';
import { Button } from '@/components/ui/button';

/** Same content as the README's "What it's for" section — reused, not
 * rewritten. That copy already earned its wording. */
const GOOD_FIT = [
  { thing: 'A product or SaaS API before launch', why: 'Every check applies, and 100 is genuinely reachable' },
  { thing: 'Internal tools and admin dashboards', why: 'Usually the worst offenders, because "it’s only internal"' },
  { thing: 'A codebase you’ve just inherited', why: '33 concrete answers beats a week of reading unfamiliar code' },
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
      {/* Hero — drop cap on the lead paragraph. */}
      <section className="mx-auto max-w-6xl px-4 pb-12 pt-16 sm:pt-24">
        <Reveal stagger={70} className="flex max-w-3xl flex-col items-start gap-5">
          <Eyebrow>Who it&rsquo;s for</Eyebrow>
          <h1 className="text-balance font-display font-semibold leading-[1.05] tracking-[-0.03em] text-[clamp(2.5rem,6vw,4rem)]">
            A backend service you&rsquo;re about to put into production.
          </h1>
          <p className="max-w-2xl text-lg leading-relaxed text-muted-foreground">
            Something containerized, that serves HTTP, talks to a database,
            and might eventually run as more than one copy. The question every
            check is really asking is{' '}
            <span className="text-foreground">&ldquo;what did we forget?&rdquo;</span>{' '}
            — not the interesting problems, the boring fatal ones.
          </p>
        </Reveal>
      </section>

      {/* Good fit / poor fit — two distinct columns, side by side, so they
          read as a comparison rather than two identical stacks. */}
      <section className="border-y bg-secondary/20">
        <div className="mx-auto grid max-w-6xl gap-12 px-4 py-16 sm:py-20 lg:grid-cols-2 lg:gap-16">
          <Reveal className="flex flex-col gap-6">
            <div className="flex items-center gap-3">
              <Check className="size-5 text-editorial" aria-hidden="true" />
              <h2 className="font-display text-2xl font-semibold tracking-tight">Good fit</h2>
            </div>
            <ul className="flex flex-col divide-y divide-border/60">
              {GOOD_FIT.map((row) => (
                <li key={row.thing} className="py-4 first:pt-0">
                  <p className="font-medium text-foreground">{row.thing}</p>
                  <p className="mt-1 text-sm text-muted-foreground">{row.why}</p>
                </li>
              ))}
            </ul>
          </Reveal>

          <Reveal className="flex flex-col gap-6">
            <div className="flex items-center gap-3">
              <Minus className="size-5 text-muted-foreground" aria-hidden="true" />
              <h2 className="font-display text-2xl font-semibold tracking-tight text-muted-foreground">
                Poor fit
              </h2>
            </div>
            <ul className="flex flex-col divide-y divide-border/60">
              {POOR_FIT.map((row) => (
                <li key={row.thing} className="py-4 first:pt-0">
                  <p className="font-medium text-foreground">{row.thing}</p>
                  <p className="mt-1 text-sm text-muted-foreground">{row.why}</p>
                </li>
              ))}
            </ul>
          </Reveal>
        </div>
      </section>

      {/* Two honest limits — a bordered editorial aside. */}
      <section className="mx-auto max-w-3xl px-4 py-16 sm:py-20">
        <Reveal className="border-l-2 border-editorial pl-6 sm:pl-8">
          <Eyebrow className="mb-4">Two honest limits</Eyebrow>
          <ul className="flex flex-col gap-4 text-muted-foreground">
            <li>
              <span className="text-foreground">
                Scores aren&rsquo;t directly comparable across very different
                projects.
              </span>{' '}
              A simple command-line tool, for example, can&rsquo;t score 100
              the same way a full web app can — some checks just don&rsquo;t
              apply to it.
            </li>
            <li>
              <span className="text-foreground">
                It reviews the code itself, not the app while it&rsquo;s
                running
              </span>{' '}
              — so it won&rsquo;t catch a problem that only shows up once the
              software is actually in use.
            </li>
          </ul>
          <p className="mt-4 text-muted-foreground">
            Think of it as a thorough pre-launch checklist, not a live
            security test.
          </p>
        </Reveal>
      </section>

      {/* CTA */}
      <section className="mx-auto max-w-6xl px-4 py-20 sm:py-28">
        <Reveal className="flex max-w-2xl flex-col items-start gap-6">
          <h2 className="text-balance font-display font-semibold leading-[1.05] tracking-[-0.02em] text-[clamp(2rem,5vw,3.25rem)]">
            Sounds like your repository?
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
