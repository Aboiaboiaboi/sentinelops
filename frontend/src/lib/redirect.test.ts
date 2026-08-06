import { describe, expect, it } from 'vitest';
import { DEFAULT_REDIRECT, safeRedirect } from './redirect';

/**
 * The post-login redirect target comes from the URL bar, so every case here is
 * an attacker's input rather than a hypothetical. The shape that matters: a
 * victim clicks a crafted link, logs in genuinely on the real domain, and is
 * then handed to the attacker's page at the exact moment they trust it most.
 */

describe('safeRedirect', () => {
  describe('keeps a real destination', () => {
    it.each(['/dashboard', '/projects/8f43f5ea-aee4-43db-a05a-cf806201b533', '/scans/1/report'])(
      'allows %s',
      (target) => {
        expect(safeRedirect(target)).toBe(target);
      },
    );

    it('keeps a query string and fragment', () => {
      expect(safeRedirect('/scans/1?tab=checks#security')).toBe('/scans/1?tab=checks#security');
    });
  });

  describe('refuses to leave the site', () => {
    it.each([
      ['//evil.com', 'protocol-relative'],
      ['/\\evil.com', 'backslash read as protocol-relative — GHSA-wrjc-x8rr-h8h6'],
      ['/\\/evil.com', 'the patch for the last one'],
      ['/\\\\evil.com', 'and the patch for that'],
      ['https://evil.com', 'absolute'],
      ['http://evil.com', 'absolute, insecure'],
      ['https:/evil.com', 'one slash, still absolute to a browser'],
      ['//evil.com/dashboard', 'a real-looking path on somebody else’s host'],
      ['javascript:alert(1)', 'not a location at all'],
      ['data:text/html,<script>alert(1)</script>', 'nor this'],
      ['evil.com', 'no leading slash, resolves relative but is not ours to trust'],
    ])('rejects %s (%s)', (target) => {
      expect(safeRedirect(target)).toBe(DEFAULT_REDIRECT);
    });

    it('rejects a backslash anywhere, not only at the start', () => {
      expect(safeRedirect('/dashboard\\..\\evil')).toBe(DEFAULT_REDIRECT);
    });
  });

  describe('refuses characters that change meaning after the browser strips them', () => {
    it.each([
      ['/\tjavascript:alert(1)', 'tab'],
      ['/\njavascript:alert(1)', 'newline'],
      ['/\rjavascript:alert(1)', 'carriage return'],
      ['/\u0000evil', 'NUL'],
      ['/ evil', 'space'],
    ])('rejects %j (%s)', (target) => {
      expect(safeRedirect(target)).toBe(DEFAULT_REDIRECT);
    });
  });

  describe('falls back rather than throwing', () => {
    // Annotated because the rows are deliberately heterogeneous — router state
    // is `unknown`, and that is the whole point of these cases.
    const cases: Array<[unknown, string]> = [
      [undefined, 'nothing remembered'],
      [null, 'explicitly nothing'],
      ['', 'empty string'],
      [42, 'not a string'],
      [{ from: '/dashboard' }, 'an object that looks plausible'],
    ];

    it.each(cases)('returns the default for %j (%s)', (target) => {
      expect(safeRedirect(target)).toBe(DEFAULT_REDIRECT);
    });
  });

  it('never returns something that could leave the origin', () => {
    // A property rather than a case list: whatever comes back is always a path
    // this app can serve, so a future edit that loosens the checks fails here
    // even if nobody thought to add its case above.
    const inputs = [
      '/ok',
      '//evil.com',
      '/\\evil.com',
      'https://evil.com',
      '',
      null,
      '/\tx',
      'evil.com',
    ];

    for (const input of inputs) {
      const result = safeRedirect(input);
      expect(result.startsWith('/')).toBe(true);
      expect(result[1] === '/' || result[1] === '\\').toBe(false);
      expect(new URL(result, 'https://sentinelops.test').origin).toBe('https://sentinelops.test');
    }
  });
});
