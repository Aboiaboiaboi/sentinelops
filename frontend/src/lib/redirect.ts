/**
 * Validation for post-login redirect targets.
 *
 * `ProtectedRoute` remembers where a logged-out visitor was headed and the
 * login page sends them there once they authenticate. That target originates
 * in the URL bar, so it is attacker-supplied: anyone can send a victim a link
 * and choose what ends up in router state.
 *
 * Used verbatim it is an open redirect, and the dangerous kind — the victim
 * lands on the attacker's page *after* a genuine login on the real domain,
 * which is the most convincing possible moment to be asked for a password
 * again. React Router 6.30.4 makes it worse by resolving a leading `/\` as
 * protocol-relative (GHSA-wrjc-x8rr-h8h6, fixed in 7.18.0), but the library
 * bug is not the mistake. Trusting a navigation target because it arrived
 * through our own router state is the mistake, and it would still be one on a
 * patched version.
 */

/** Where a visitor with no remembered destination goes. */
export const DEFAULT_REDIRECT = '/dashboard';

/** Control characters, plus space. Browsers strip tab, newline and carriage
 * return *before* parsing a URL, so a target can pass a naive check and then be
 * read as something else once the stripping happens. */
// eslint-disable-next-line no-control-regex
const UNSAFE_CHARACTERS = /[\u0000-\u0020\u007f]/;

/**
 * A redirect target reduced to somewhere on this site, or the default.
 *
 * Deliberately an allow-list of one shape — a single leading slash followed by
 * something that is not another slash or a backslash — rather than a list of
 * bad prefixes to strip. Blocklists here have a long history of being one
 * encoding away from useless: `//evil.com`, `/\evil.com`, `/\/evil.com` and
 * `https:/evil.com` are the same attack wearing different hats, and each one
 * was somebody's patch for the last one.
 */
export function safeRedirect(target: unknown): string {
  if (typeof target !== 'string' || target.length === 0) return DEFAULT_REDIRECT;

  // Must be an absolute path on this origin.
  if (!target.startsWith('/')) return DEFAULT_REDIRECT;

  // `//host` is protocol-relative and `/\host` is treated as such by enough
  // parsers to count. Both leave the site while looking like a path.
  if (target.length > 1 && (target[1] === '/' || target[1] === '\\')) return DEFAULT_REDIRECT;

  // A backslash anywhere is rejected rather than normalised: no route in this
  // app contains one, so it is only ever an attempt to be read as a separator
  // by whichever parser is least careful.
  if (target.includes('\\')) return DEFAULT_REDIRECT;

  if (UNSAFE_CHARACTERS.test(target)) return DEFAULT_REDIRECT;

  return target;
}
