import { useCallback, useRef } from 'react';
import gsap from 'gsap';

/**
 * A small "magnetic" pull toward the cursor, with a bouncy snap back on
 * release — applied to every button (see components/ui/button.tsx) so the
 * whole app gets the same tactile feel rather than one-off animations per
 * page.
 *
 * Checked once per event rather than cached at mount, the same reasoning
 * Reveal.tsx uses for prefers-reduced-motion: a user can change either
 * setting mid-session (switching from a mouse to a touchpad, say) and the
 * next interaction should honour it immediately, not whatever was true when
 * the button first rendered.
 */
const MAX_OFFSET_PX = 8;
const PULL_STRENGTH = 0.3;

function motionAllowed(): boolean {
  // jsdom (this project's test environment) has no matchMedia at all — every
  // button in the app fires onMouseDown/onMouseUp under @testing-library's
  // userEvent, so an unguarded call here doesn't just skip the animation in
  // tests, it throws on essentially every existing interaction test.
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  return (
    !window.matchMedia('(prefers-reduced-motion: reduce)').matches &&
    window.matchMedia('(hover: hover) and (pointer: fine)').matches
  );
}

export function useMagneticHover<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);

  const onMouseMove = useCallback((event: React.MouseEvent<T>) => {
    const el = ref.current;
    if (!el || !motionAllowed()) return;
    const rect = el.getBoundingClientRect();
    const x = gsap.utils.clamp(
      -MAX_OFFSET_PX,
      MAX_OFFSET_PX,
      (event.clientX - (rect.left + rect.width / 2)) * PULL_STRENGTH,
    );
    const y = gsap.utils.clamp(
      -MAX_OFFSET_PX,
      MAX_OFFSET_PX,
      (event.clientY - (rect.top + rect.height / 2)) * PULL_STRENGTH,
    );
    gsap.to(el, { x, y, duration: 0.35, ease: 'power2.out' });
  }, []);

  const onMouseLeave = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    // Elastic rather than power2 here — this is the "bounce," the moment the
    // button overshoots home and settles, not just a return to zero.
    gsap.to(el, { x: 0, y: 0, scale: 1, duration: 0.6, ease: 'elastic.out(1, 0.4)' });
  }, []);

  const onMouseDown = useCallback(() => {
    const el = ref.current;
    if (!el || !motionAllowed()) return;
    gsap.to(el, { scale: 0.94, duration: 0.15, ease: 'power2.out' });
  }, []);

  const onMouseUp = useCallback(() => {
    const el = ref.current;
    if (!el || !motionAllowed()) return;
    gsap.to(el, { scale: 1, duration: 0.5, ease: 'elastic.out(1, 0.35)' });
  }, []);

  return { ref, onMouseMove, onMouseLeave, onMouseDown, onMouseUp };
}
