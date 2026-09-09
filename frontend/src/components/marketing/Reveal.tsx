import { type ReactNode, useRef } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';

gsap.registerPlugin(ScrollTrigger);

interface RevealProps {
  children: ReactNode;
  className?: string;
  /** Stagger delay (ms) between direct children — set only when the
   * children should reveal one after another rather than together. */
  stagger?: number;
  /** Extra scroll-trigger start offset, e.g. "top 85%". */
  start?: string;
}

/**
 * The one scroll-reveal wrapper every marketing section uses: fade + small
 * rise as the element enters the viewport (GSAP's own "Subtle" tier — an
 * 8-16px offset so it reads as a fade, not a slide).
 *
 * Reduced motion is checked once, synchronously, rather than through
 * gsap.matchMedia() — useGSAP's own `scope` already gives automatic,
 * StrictMode-safe cleanup (kills/reverts on unmount and on every dev-mode
 * double-invoke), and layering a second, manually-reverted matchMedia
 * context on top of that fought with it: the manual revert() could kill a
 * tween mid-flight, leaving it frozen at whatever opacity it happened to be
 * at. One cleanup system, not two.
 */
export function Reveal({ children, className, stagger, start = 'top 85%' }: RevealProps) {
  const ref = useRef<HTMLDivElement>(null);

  useGSAP(
    () => {
      const targets = stagger ? gsap.utils.toArray<HTMLElement>(ref.current!.children) : ref.current;
      if (!targets || (Array.isArray(targets) && targets.length === 0)) return;

      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        gsap.set(targets, { opacity: 1, y: 0 });
        return;
      }

      gsap.fromTo(
        targets,
        { opacity: 0, y: 16 },
        {
          opacity: 1,
          y: 0,
          duration: 0.6,
          ease: 'power2.out',
          stagger: stagger ? stagger / 1000 : 0,
          scrollTrigger: {
            trigger: ref.current,
            start,
            // Fires forward exactly once. No "reverse" leg — this is a
            // one-time entrance, not a hide-again-on-scroll-up effect, and
            // `reverse` was the actual bug: a ScrollTrigger.refresh() after
            // fonts/layout settled could re-evaluate position and fire a
            // spurious reverse mid-tween, fighting the forward animation
            // and freezing it at whatever opacity the two collided at.
            once: true,
          },
        },
      );
    },
    { scope: ref, dependencies: [stagger, start] },
  );

  return (
    <div ref={ref} className={className}>
      {children}
    </div>
  );
}
