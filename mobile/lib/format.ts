/** The server stores UTC as 'YYYY-MM-DD HH:MM:SS'. */
export function parseTs(value: string | null | undefined): Date | null {
  if (!value) return null;
  const d = new Date(`${value.slice(0, 19).replace(' ', 'T')}Z`);
  return isNaN(d.getTime()) ? null : d;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export function day(value: string | null | undefined): string {
  const d = parseTs(value);
  return d ? `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}` : '';
}

export function when(value: string | null | undefined): string {
  const d = parseTs(value);
  if (!d) return '';
  const h = d.getHours() % 12 || 12;
  const m = String(d.getMinutes()).padStart(2, '0');
  return `${d.getDate()} ${MONTHS[d.getMonth()]}, ${h}:${m} ${d.getHours() < 12 ? 'am' : 'pm'}`;
}

export function ago(value: string | null | undefined): string {
  const d = parseTs(value);
  if (!d) return '';
  const s = (Date.now() - d.getTime()) / 1000;
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 14) return `${Math.floor(s / 86400)} d ago`;
  return day(value);
}

/** "5h 12m left" style, for the quote window. */
export function countdown(secondsLeft: number): string {
  if (secondsLeft <= 0) return 'Time’s up';
  const h = Math.floor(secondsLeft / 3600);
  const m = Math.floor((secondsLeft % 3600) / 60);
  const s = Math.floor(secondsLeft % 60);
  if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m left`;
  return `${m}m ${String(s).padStart(2, '0')}s left`;
}

export function money(v: number | null | undefined): string {
  if (v === null || v === undefined) return '';
  return `$${Math.round(v).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',')}`;
}

export const JOB_STATUS: Record<string, { label: string; tone: 'chalk' | 'pine' | 'paint' | 'muted' }> = {
  open: { label: 'Taking quotes', tone: 'chalk' },
  full: { label: 'All quotes in', tone: 'chalk' },
  held: { label: 'Confirm your phone', tone: 'paint' },
  hired: { label: 'Hired', tone: 'pine' },
  closed: { label: 'Closed', tone: 'muted' },
  expired: { label: 'Closed', tone: 'muted' },
};

export const QUOTE_STATUS: Record<string, { label: string; tone: 'chalk' | 'pine' | 'paint' | 'muted' }> = {
  sent: { label: 'Sent', tone: 'chalk' },
  shortlisted: { label: 'Contact shared', tone: 'paint' },
  accepted: { label: 'Accepted', tone: 'pine' },
  declined: { label: 'Not chosen', tone: 'muted' },
};
