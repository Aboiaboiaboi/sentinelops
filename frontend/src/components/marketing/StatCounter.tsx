import { useRef } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';
import { ScrollTrigger } from 'gsap/ScrollTrigger';

gsap.registerPlugin(ScrollTrigger);

interface StatCounterProps {
  /** The number to count up to. */
  value: number;
  /** Rendered after the number, e.g. "/100" or "+". */
  suffix?: string;
  className?: string;
}

/**
 * Counts up from 0 to `value` once, the moment it scrolls into view.
 * Reduced-motion users just see the final number rendered immediately —
 * a count animation is decorative, not information, so there's nothing
 * to preserve for them. See Reveal.tsx for why this checks the media
 * query directly rather than through gsap.matchMedia().
 */
export function StatCounter({ value, suffix = '', className }: StatCounterProps) {
  const ref = useRef<HTMLSpanElement>(null);

  useGSAP(
    () => {
      const el = ref.current;
      if (!el) return;

      if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        el.textContent = `${value}${suffix}`;
        return;
      }

      const counter = { n: 0 };
      gsap.to(counter, {
        n: value,
        duration: 1.2,
        ease: 'power1.out',
        scrollTrigger: {
          trigger: el,
          start: 'top 90%',
          once: true,
        },
        onUpdate: () => {
          el.textContent = `${Math.round(counter.n)}${suffix}`;
        },
      });
    },
    { scope: ref, dependencies: [value, suffix] },
  );

  return (
    <span ref={ref} className={className}>
      0{suffix}
    </span>
  );
}
