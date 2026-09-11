import { Link } from 'react-router-dom';
import { ArrowRight, ShieldCheck, FileSearch, Boxes } from 'lucide-react';
import { Logo } from '@/components/Logo';
import { Eyebrow } from '@/components/Eyebrow';
import { Reveal } from '@/components/marketing/Reveal';
import { SectionHeading } from '@/components/marketing/SectionHeading';
import { StatCounter } from '@/components/marketing/StatCounter';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';

/** Real self-scan output — see the top of the project's own README. Not a
 * mockup: this is what running SentinelOps against SentinelOps returns. */
const CATEGORY_SCORES = [
  { name: 'Security', score: 25, max: 25 },
  { name: 'Reliability', score: 20, max: 20 },
  { name: 'Deployment', score: 12, max: 17 },
  { name: 'Architecture', score: 14, max: 14 },
  { name: 'Scalability', score: 14, max: 14 },
  { name: 'Observability', score: 10, max: 10 },
];

const FEATURES = [
  {
    icon: ShieldCheck,
    title: 'Real tools, not guesswork',
    description:
      'Gitleaks, Trivy, Semgrep, Hadolint, and Checkov — the same tools teams use in production — run inside isolated sandboxes with no network access, not a pile of regexes pretending to be a scanner.',
  },
  {
    icon: FileSearch,
    title: 'Shows its work',
    description:
      'Every one of the 33 checks reports passed, flagged, skipped, or errored — with a reason. A category at full marks tells you what it verified, not just that it found nothing to complain about.',
  },
  {
    icon: Boxes,
    title: 'Sandboxed by design',
    description:
      'No network, read-only filesystem, every extra Linux capability dropped, non-root, memory-capped. The one part of this system that runs a stranger’s code is the part treated as hostile.',
  },
];

const STATS = [
  { value: 95, suffix: '/100', label: 'Self-scan score' },
  { value: 33, label: 'Checks run' },
  { value: 6, label: 'Categories' },
  { value: 5, label: 'Sandboxed tools' },
];

export default function LandingPage() {
  return (
    <>
      {/* Hero — asymmetric: the headline block down the left, the real
          self-scan offset below-right rather than stacked and centred. */}
      <section className="mx-auto max-w-6xl px-4 pb-20 pt-16 sm:pb-28 sm:pt-24">
        <div className="grid gap-12 lg:grid-cols-12 lg:gap-8">
          <Reveal stagger={70} className="flex flex-col items-start gap-6 lg:col-span-7">
            <Logo size="lg" />
            <Eyebrow>Production-readiness scanner</Eyebrow>
            <h1 className="max-w-[14ch] text-balance font-display font-semibold leading-[1.02] tracking-[-0.03em] text-[clamp(2.75rem,7vw,4.75rem)]">
              Know what&rsquo;s wrong before you ship.
            </h1>
            <p className="max-w-lg text-lg text-muted-foreground">
              Point SentinelOps at a repository. It clones it, runs 33 checks
              across six categories, and hands back a score out of 100 with
              exactly what to fix.
            </p>
            <div className="flex flex-wrap items-center gap-3">
              <Button asChild size="lg">
                <Link to="/signup">
                  Scan a repository
                  <ArrowRight className="size-4" />
                </Link>
              </Button>
              <Button asChild size="lg" variant="ghost">
                <Link to="/how-it-works">How it works</Link>
              </Button>
            </div>
          </Reveal>

          <Reveal
            start="top 90%"
            className="lg:col-span-5 lg:col-start-8 lg:mt-24 lg:self-start"
          >
            <Card className="overflow-hidden border-border/60 bg-card/80 font-mono text-sm shadow-2xl shadow-primary/5">
              <CardContent className="p-6">
                <div className="mb-4 flex items-center justify-between">
                  <span className="text-muted-foreground">sentinelops --self-scan</span>
                  <span className="rounded bg-primary-bright/10 px-2 py-0.5 text-primary-bright">
                    95 / 100 &middot; Grade A
                  </span>
                </div>
                <div className="space-y-2">
                  {CATEGORY_SCORES.map((c) => (
                    <div key={c.name} className="flex items-center gap-3">
                      <span className="w-28 shrink-0 text-muted-foreground">{c.name}</span>
                      <div className="h-2 flex-1 overflow-hidden rounded-full bg-secondary">
                        <div
                          className="h-full rounded-full bg-primary-bright"
                          style={{ width: `${(c.score / c.max) * 100}%` }}
                        />
                      </div>
                      <span className="w-14 shrink-0 text-right text-muted-foreground">
                        {c.score}/{c.max}
                      </span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </Reveal>
        </div>
      </section>

      {/* Feature cards — offset grid so the three don't read as one flat row. */}
      <section className="mx-auto max-w-6xl px-4 pb-20 sm:pb-28">
        <Reveal className="mb-10">
          <SectionHeading number="01" eyebrow="What it does">
            Three things, done properly.
          </SectionHeading>
        </Reveal>
        <Reveal stagger={40} className="grid gap-6 md:grid-cols-3">
          {FEATURES.map((feature, i) => (
            <Card
              key={feature.title}
              className={
                'border-border/60 bg-card/60 transition-transform duration-200 hover:-translate-y-1 ' +
                (i === 1 ? 'md:mt-10' : '')
              }
            >
              <CardContent className="flex flex-col gap-4 p-6">
                <feature.icon className="size-8 text-editorial" aria-hidden="true" />
                <h3 className="font-display text-xl font-semibold">{feature.title}</h3>
                <p className="text-sm leading-relaxed text-muted-foreground">
                  {feature.description}
                </p>
              </CardContent>
            </Card>
          ))}
        </Reveal>
      </section>

      {/* Proof strip — the numbers, with mono kicker labels and accent figures. */}
      <section className="border-y bg-secondary/20">
        <Reveal
          stagger={40}
          className="mx-auto grid max-w-6xl grid-cols-2 gap-x-8 gap-y-10 px-4 py-14 sm:grid-cols-4"
        >
          {STATS.map((s) => (
            <div key={s.label} className="flex flex-col gap-2">
              <Eyebrow className="text-muted-foreground">{s.label}</Eyebrow>
              <div className="font-display text-4xl font-semibold tracking-tight text-editorial sm:text-5xl">
                <StatCounter value={s.value} suffix={s.suffix} />
              </div>
            </div>
          ))}
        </Reveal>
      </section>

      {/* Closing CTA */}
      <section className="mx-auto max-w-6xl px-4 py-20 sm:py-28">
        <Reveal className="flex max-w-2xl flex-col items-start gap-6">
          <h2 className="text-balance font-display font-semibold leading-[1.05] tracking-[-0.02em] text-[clamp(2rem,5vw,3.25rem)]">
            Find out what your repository actually looks like.
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
