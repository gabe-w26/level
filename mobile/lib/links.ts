import type { Role } from './api';

/**
 * Notifications carry the website path they're about (e.g. /trade/jobs/12).
 * This turns one into the matching app screen, so tapping a push or a
 * notification lands on the right thing. Anything without an app screen
 * goes to the notifications list.
 */
export function appRouteFor(link: string | null | undefined, role: Role | undefined): string {
  const path = (link || '').split('#')[0].split('?')[0];
  let m: RegExpMatchArray | null;
  if ((m = path.match(/^\/trade\/jobs\/(\d+)/))) return `/offer/${m[1]}`;
  if ((m = path.match(/^\/me\/jobs\/(\d+)/))) return `/job/${m[1]}`;
  if ((m = path.match(/^\/thread\/(\d+)\/(\d+)/))) return `/thread/${m[1]}/${m[2]}`;
  if (path === '/trade/quotes') return '/(trade)/quotes';
  if (path === '/trade' || path === '/trade/') return '/(trade)';
  if (path === '/me' || path === '/me/') return '/(customer)';
  if (path === '/messages') return role === 'trade' ? '/(trade)/messages' : '/(customer)/messages';
  return '/notifications';
}
