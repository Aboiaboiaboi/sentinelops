import { Link } from 'react-router-dom';
import { ArrowRight, ShieldCheck, FileSearch, Boxes } from 'lucide-react';
import { Logo } from '@/components/Logo';
import { Reveal } from '@/components/marketing/Reveal';
import { StatCounter } from '@/components/marketing/StatCounter';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card';

/** Real self-scan output — see the top of the project's own README. Not a
 * mockup: this is what running SentinelOps against SentinelOps returns. */
const CATEGORY_SCORES = [
  { name: 'Security', score: 25, max: 25 },
  { name: 'Reliability', score: 20, max: 20 },
  { name: 'Deployment', score: 11, max: 17 },
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
      'Every one of the 33 checks reports passed, failed, skipped, or errored — with a reason. A category at full marks tells you what it verified, not just that it found nothing to complain about.',
  },
  {
    icon: Boxes,
    title: 'Sandboxed by design',
    description:
      'No network, read-only filesystem, every extra Linux capability dropped, non-root, memory-capped. The one part of this system that runs a stranger’s code is the part treated as hostile.',
  },
];

export default function LandingPage() {
  return (
    <>
      {/* Hero */}
      <section className="mx-auto max-w-6xl px-4 pb-16 pt-16 sm:pb-24 sm:pt-24">
        <Reveal stagger={70} className="flex flex-col items-center gap-6 text-center">
          <Logo size="lg" />
          <h1 className="max-w-3xl text-balance font-display text-4xl font-semibold tracking-tight sm:text-6xl">
            Know what&rsquo;s wrong before you ship.
          </h1>
          <p className="max-w-xl text-balance text-lg text-muted-foreground">
            Point SentinelOps at a repository. It clones it, runs 33 checks
            across six categories, and hands back a score out of 100 with
            exactly what to fix.
          </p>
          <div className="flex flex-wrap items-center justify-center gap-3">
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

        {/* Terminal-style proof: the real self-scan, rendered as data. */}
        <Reveal start="top 90%" className="mx-auto mt-16 max-w-2xl">
          <Card className="overflow-hidden border-border/60 bg-card/80 font-mono text-sm shadow-2xl shadow-primary/5">
            <CardContent className="p-6">
              <div className="mb-4 flex items-center justify-between">
                <span className="text-muted-foreground">sentinelops --self-scan</span>
                <span className="rounded bg-primary-bright/10 px-2 py-0.5 text-primary-bright">
                  94 / 100 &middot; Grade A
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
      </section>

      {/* Feature cards */}
      <section className="mx-auto max-w-6xl px-4 pb-16 sm:pb-24">
        <Reveal stagger={40} className="grid gap-6 sm:grid-cols-3">
          {FEATURES.map((feature) => (
            <Card key={feature.title} className="border-border/60 bg-card/60">
              <CardHeader>
                <feature.icon className="size-8 text-primary-bright" />
                <CardTitle className="font-display text-xl">{feature.title}</CardTitle>
              </CardHeader>
              <CardContent>
                <CardDescription className="text-sm leading-relaxed">
                  {feature.description}
                </CardDescription>
              </CardContent>
            </Card>
          ))}
        </Reveal>
      </section>

      {/* Proof strip */}
      <section className="border-y bg-secondary/30">
        <Reveal
          stagger={40}
          className="mx-auto grid max-w-6xl grid-cols-2 gap-8 px-4 py-12 text-center sm:grid-cols-4"
        >
          <div>
            <div className="font-mono text-3xl font-medium text-primary-bright sm:text-4xl">
              <StatCounter value={94} suffix="/100" />
            </div>
            <p className="mt-1 text-sm text-muted-foreground">Self-scan score</p>
          </div>
          <div>
            <div className="font-mono text-3xl font-medium text-primary-bright sm:text-4xl">
              <StatCounter value={33} />
            </div>
            <p className="mt-1 text-sm text-muted-foreground">Checks run</p>
          </div>
          <div>
            <div className="font-mono text-3xl font-medium text-primary-bright sm:text-4xl">
              <StatCounter value={6} />
            </div>
            <p className="mt-1 text-sm text-muted-foreground">Categories</p>
          </div>
          <div>
            <div className="font-mono text-3xl font-medium text-primary-bright sm:text-4xl">
              <StatCounter value={5} />
            </div>
            <p className="mt-1 text-sm text-muted-foreground">Sandboxed tools</p>
          </div>
        </Reveal>
      </section>

      {/* Closing CTA */}
      <section className="mx-auto max-w-6xl px-4 py-16 text-center sm:py-24">
        <Reveal className="flex flex-col items-center gap-6">
          <h2 className="font-display text-3xl font-semibold tracking-tight sm:text-4xl">
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
