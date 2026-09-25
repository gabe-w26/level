import * as SecureStore from 'expo-secure-store';
import { API_URL } from './config';

const TOKEN_KEY = 'level_token';

export async function getToken(): Promise<string | null> {
  return SecureStore.getItemAsync(TOKEN_KEY);
}
export async function saveToken(token: string): Promise<void> {
  await SecureStore.setItemAsync(TOKEN_KEY, token);
}
export async function clearToken(): Promise<void> {
  await SecureStore.deleteItemAsync(TOKEN_KEY);
}

/** An error from the server, with per-field messages when a form was wrong. */
export class ApiError extends Error {
  status: number;
  errors: Record<string, string>;
  constructor(message: string, status: number, errors: Record<string, string> = {}) {
    super(message);
    this.status = status;
    this.errors = errors;
  }
}

/** Called when the server says the token is no good, so the app can sign out. */
let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn;
}

type Method = 'GET' | 'POST' | 'DELETE';

async function request<T>(method: Method, path: string, body?: object | FormData): Promise<T> {
  const token = await getToken();
  const headers: Record<string, string> = { Accept: 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body;                    // fetch sets the multipart boundary itself
  } else if (body) {
    headers['Content-Type'] = 'application/json';
    payload = JSON.stringify(body);
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), body instanceof FormData ? 120000 : 30000);
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, { method, headers, body: payload, signal: controller.signal });
  } catch (e) {
    throw new ApiError('Can’t reach Level right now. Check your connection and try again.', 0);
  } finally {
    clearTimeout(timer);
  }

  let data: any = null;
  try {
    data = await res.json();
  } catch (_) {
    // Not JSON (e.g. the host's error page while the server wakes up)
  }
  if (!res.ok) {
    if (res.status === 401 && token && onUnauthorized) onUnauthorized();
    throw new ApiError(data?.error || `Something went wrong (${res.status}). Try again.`, res.status, data?.errors || {});
  }
  return data as T;
}

const get = <T>(path: string) => request<T>('GET', path);
const post = <T>(path: string, body?: object | FormData) => request<T>('POST', path, body || {});
const del = <T>(path: string, body?: object) => request<T>('DELETE', path, body || {});

// ── Shapes ────────────────────────────────────────────────────────────────────

export type Role = 'customer' | 'trade';

export interface TradeStatus {
  business_name: string;
  setup_complete: boolean;
  active: boolean;
  needs_web_setup: boolean;
  setup_message: string | null;
  setup_url: string | null;
  paused: boolean;
  paused_until: string | null;
}

export interface User {
  id: number;
  role: Role;
  name: string;
  email: string;
  username: string | null;
  phone: string | null;
  email_verified: boolean;
  phone_verified: boolean;
  trade?: TradeStatus | null;
}

export interface Counts { notifications: number; messages: number; offers: number; }

export interface Option { key: string; label: string; }
export interface AppConfig {
  brand: string;
  categories: { id: number; slug: string; name: string; licence_note: string | null }[];
  regions: { region: string; areas: { id: number; slug: string; name: string }[] }[];
  value_bands: (Option & { short: string })[];
  timing: Option[];
  property_types: Option[];
  max_quotes: number;
  offer_window_hours: number;
  trades_per_job: number;
  max_photos: number;
  contract_threshold: number;
  support_email: string;
  links: { site: string; terms: string; privacy: string };
}

export interface Job {
  id: number;
  title: string;
  description: string;
  suburb: string | null;
  address?: string | null;
  value_band: string;
  value_label: string | null;
  timing: string | null;
  timing_label: string | null;
  property_type: string | null;
  property_label: string | null;
  status: 'open' | 'full' | 'held' | 'hired' | 'closed' | 'expired';
  quote_count: number;
  max_quotes: number;
  created_at: string;
  closes_at: string;
  closed_at: string | null;
  close_reason: string | null;
  work_done_on?: string | null;
  category_name?: string;
  area_name?: string;
  licence_note?: string | null;
}

export interface Rating { n: number; avg: number | null; }

export type ReportKind = 'daily' | 'weekly' | 'monthly';
/** How often a trade keeps its progress-update promises, across hired jobs. null until there's enough to judge. */
export interface ReportRecord { pct: number; kept: number; expected: number; jobs: number; }

export interface ProgressUpdate { id: number; body: string; kinds: ReportKind[]; local_date: string; created_at: string; photos: string[]; }

export interface Progress {
  report_plan: ReportKind[];
  report_plan_text: string;
  work_started_on: string | null;
  work_done_on: string | null;
  score: Partial<Record<ReportKind, { kept: number; expected: number; due: boolean; label: string; word: string }>>;
  due: ReportKind[];
  updates: ProgressUpdate[];
  today: string;
  max_photos: number;
}

export interface Quote {
  id: number;
  job_id: number;
  trade_id: number;
  price_type: 'fixed' | 'range' | 'site_visit';
  amount_low: number | null;
  amount_high: number | null;
  gst_included: boolean;
  message: string;
  inclusions: string | null;
  exclusions: string | null;
  warranty: string | null;
  available_from: string | null;
  duration: string | null;
  status: 'sent' | 'shortlisted' | 'accepted' | 'declined';
  created_at: string;
  price_text: string;
  act_docs_promised: boolean;
  /** What this quote covers that the others don't, and the GST trap. From compare.py. */
  notes?: { kind: string; text: string }[];
  /** Where the money goes, when the trade broke the price down. */
  items?: QuoteItem[];
  /** How well checked this business is. Shown, never used to pick who gets a job. */
  trust?: TrustSummary;
  business_name?: string;
  licence_type?: string | null;
  licence_checked?: boolean;
  insurance_checked?: boolean;
  nzbn_checked?: boolean;
  years_trading?: number | null;
  workmanship_guarantee?: string | null;
  msg_count?: number;
  rating?: Rating;
  needs_act?: boolean;
  contact?: { name: string; phone: string | null; email: string };
  report_plan?: ReportKind[];
  report_plan_text?: string;
  report_record?: ReportRecord | null;
  // On a trade's own list
  title?: string;
  suburb?: string;
  area_name?: string;
  category_name?: string;
  job_status?: Job['status'];
  unread?: number;
  quote_count?: number;
}

export interface CustomerJobDetail {
  job: Job;
  quotes: Quote[];
  stats: { offered: number; waiting: number; quoted: number; passed: number; next_expiry: string | null };
  photos: string[];
  reviewed: boolean;
  can_review: boolean;
  can_close: boolean;
  held: boolean;
  progress: Progress | null;
  site: Site | null;
}

export interface Offer extends Job {
  offer_id: number;
  expires_at: string;
  offered_at: string;
  seen: boolean;
  seconds_left: number;
}

export interface QuoteTemplate {
  id: number; name: string; price_type: string | null; message: string | null;
  inclusions: string | null; exclusions: string | null; warranty: string | null; duration: string | null;
  items?: QuoteItem[];
}

export interface QuoteItem {
  description: string;
  qty: number | null;
  unit: string | null;
  unit_price: number | null;
  total?: number | null;
}

export interface TrustSummary {
  score: number;
  band: string;
  /** Two or three plain sentences naming only what was actually checked. */
  lines: string[];
}

/** The site, once someone is hired. Null before that. */
export interface Site {
  fields: { key: string; label: string; hint: string; value: string }[];
  filled: number;
  notes: SiteNote[];
  check_items: { key: string; question: string; why: string }[];
  checks: SiteCheck[];
}

export interface SiteNote {
  id: number; body: string; shared: boolean; who: string; created_at: string;
  can_delete: boolean; photos: string[];
}

export interface SiteCheck {
  id: number; created_at: string; flags: string[];
  hazards: string | null; notes: string | null;
  items: { question: string; label: string }[];
}

export interface TradeJobDetail {
  job: Job;
  offer: { status: 'active' | 'quoted' | 'expired' | 'declined' | 'closed'; expires_at: string; offered_at: string; seconds_left: number };
  quote: Quote | null;
  can_edit_quote: boolean;
  contact: { name: string; phone: string | null; email: string } | null;
  can_quote: boolean;
  customer_record: { quotes: number; answered: number; jobs: number; hired: number; since: string | null; phone_verified: boolean };
  photos: string[];
  templates: QuoteTemplate[];
  contract_threshold: number;
  default_report_plan: ReportKind[];
  progress: Progress | null;
  items: QuoteItem[];
  units: string[];
  site: Site | null;
}

export interface Thread {
  job_id: number; trade_id: number; title: string; with: string;
  last_body: string | null; last_at: string; unread: number;
}

export interface Message { id: number; body: string; created_at: string; mine: boolean; }

export interface Notice { id: number; body: string; link: string | null; created_at: string; read: boolean; }

export interface TradeProfile {
  business_name: string; about: string | null; years_trading: number | null;
  licence_type: string | null; licence_number: string | null;
  licence_checked: boolean; insurance_checked: boolean; nzbn_checked: boolean;
  workmanship_guarantee: string | null; categories: string[]; areas: string[];
  rating: Rating; public_url: string; edit_url: string | null;
  report_plan: ReportKind[]; report_record: ReportRecord | null;
}

export interface QuoteInput {
  price_type: 'fixed' | 'range' | 'site_visit';
  amount_low?: string;
  amount_high?: string;
  gst: 'incl' | 'excl';
  message: string;
  inclusions?: string;
  exclusions?: string;
  warranty?: string;
  available_from?: string;
  duration?: string;
  act_docs_promised?: boolean;
  report_plan?: ReportKind[];
}

export interface JobInput {
  category: string; area: string; suburb: string; address?: string; title: string; description: string;
  value_band: string; timing: string; property_type: string;
}

export interface PhotoInput { uri: string; name: string; type: string; }

type Ok = { ok: true; message?: string };

export interface TradeReferrals {
  invite_url: string;
  months: { waiting: number; used: number; earned: number; cap: number };
  rewards: { business_name: string | null; months: number; earned_at: string; used: boolean }[];
  joined: { business_name: string; joined_at: string; quoted: boolean }[];
}

export interface CustomerShare {
  share_url: string;
  friends: number;
  email_on: boolean;
  recommended: { business_name: string; category_name: string; joined: boolean }[];
}

export interface RecommendInput {
  name: string; business_name?: string; email?: string; phone?: string; note?: string;
  category_id: number | ''; area_id: number | ''; email_them?: boolean;
}

// ── Calls ─────────────────────────────────────────────────────────────────────

export const api = {
  config: () => get<AppConfig>('/config'),

  login: (login: string, password: string, device?: string) =>
    post<{ token: string; user: User }>('/login', { login, password, device }),
  signup: (fields: { role: Role; name: string; email: string; phone: string; password: string; business_name?: string; device?: string }) =>
    post<{ token: string; user: User }>('/signup', fields),
  forgot: (email: string) => post<{ ok: true; message: string }>('/forgot', { email }),
  logout: (pushToken?: string | null) => post<Ok>('/logout', { push_token: pushToken || undefined }),
  me: () => get<{ user: User; counts: Counts }>('/me'),
  deleteAccount: (password: string) => del<Ok>('/me', { password }),

  registerPushToken: (token: string, platform: string) => post<Ok>('/push-tokens', { token, platform }),
  unregisterPushToken: (token: string) => del<Ok>('/push-tokens', { token }),

  sendPhoneCode: () => post<{ ok: true; sent?: boolean; verified?: boolean; texts_on?: boolean }>('/verify-phone/send'),
  verifyPhone: (code: string) => post<{ ok: true; released_to: number }>('/verify-phone', { code }),

  notifications: () => get<{ notifications: Notice[]; counts: Counts }>('/notifications'),
  readAllNotifications: () => post<Ok>('/notifications/read'),
  readNotification: (id: number) => post<Ok>(`/notifications/${id}/read`),

  threads: () => get<{ threads: Thread[] }>('/threads'),
  thread: (jobId: number, tradeId: number) =>
    get<{ job: { id: number; title: string; status: string }; with: string; messages: Message[] }>(`/threads/${jobId}/${tradeId}`),
  sendMessage: (jobId: number, tradeId: number, body: string) => post<Ok>(`/threads/${jobId}/${tradeId}`, { body }),

  // Customer
  myJobs: () => get<{ jobs: Job[] }>('/customer/jobs'),
  customerJob: (id: number) => get<CustomerJobDetail>(`/customer/jobs/${id}`),
  postJob: (fields: JobInput, photos: PhotoInput[]) => {
    const form = new FormData();
    Object.entries(fields).forEach(([k, v]) => { if (v) form.append(k, String(v)); });
    // React Native's FormData takes {uri, name, type} for files
    photos.forEach((p) => form.append('photos', p as unknown as Blob));
    return post<{ job_id: number; offered: number; held: boolean; message: string }>('/customer/jobs', form);
  },
  quoteAction: (jobId: number, quoteId: number, action: 'share' | 'accept' | 'decline', actAck = false) =>
    post<Ok>(`/customer/jobs/${jobId}/quotes/${quoteId}/${action}`, { act_ack: actAck }),
  closeJob: (jobId: number, outcome: 'elsewhere' | 'not_going_ahead') =>
    post<Ok>(`/customer/jobs/${jobId}/close`, { outcome }),
  customerFinish: (jobId: number) => post<Ok>(`/customer/jobs/${jobId}/finish`),
  customerShare: () => get<CustomerShare>('/customer/share'),
  recommend: (fields: RecommendInput) =>
    post<Ok & { already_on_level: boolean; emailed?: boolean; join_url?: string; profile_url?: string }>('/customer/recommend', fields),
  reviewParts: () => get<{ parts: Option[] }>('/review-parts'),
  review: (jobId: number, scores: Record<string, number>, body: string) =>
    post<Ok>(`/customer/jobs/${jobId}/review`, { ...scores, body }),

  // Trade
  offers: () => get<{ offers: Offer[]; server_time: string; trade: TradeStatus }>('/trade/offers'),
  tradeJob: (id: number) => get<TradeJobDetail>(`/trade/jobs/${id}`),
  saveSite: (jobId: number, fields: Record<string, string>) =>
    post<{ ok: true; message: string }>(`/jobs/${jobId}/site`, fields),
  addSiteNote: (jobId: number, body: string, isPrivate = false) =>
    post<{ ok: true; message: string }>(`/jobs/${jobId}/notes`, { body, private: isPrivate }),
  deleteSiteNote: (jobId: number, noteId: number) =>
    del<{ ok: true; message: string }>(`/jobs/${jobId}/notes/${noteId}`),
  saveSiteCheck: (jobId: number, answers: Record<string, string>, hazards: string, notes: string) =>
    post<{ ok: true; message: string }>(`/trade/jobs/${jobId}/site-check`, { answers, hazards, notes }),
  sendQuote: (jobId: number, q: QuoteInput) =>
    post<{ ok: true; quote_number: number; max_quotes: number; message: string }>(`/trade/jobs/${jobId}/quote`, q),
  editQuote: (jobId: number, q: QuoteInput) => post<Ok>(`/trade/jobs/${jobId}/quote/edit`, q),
  passJob: (jobId: number) => post<Ok>(`/trade/jobs/${jobId}/pass`),
  myQuotes: () => get<{ quotes: Quote[] }>('/trade/quotes'),
  tradeProfile: () => get<{ profile: TradeProfile; status: TradeStatus }>('/trade/profile'),
  postUpdate: (jobId: number, body: string, kinds: ReportKind[], photos: PhotoInput[]) => {
    const form = new FormData();
    form.append('body', body);
    kinds.forEach((k) => form.append('kinds', k));
    photos.forEach((p) => form.append('photos', p as unknown as Blob));
    return post<Ok & { id: number; progress: Progress }>(`/trade/jobs/${jobId}/updates`, form);
  },
  setStart: (jobId: number, start: string) => post<Ok & { progress: Progress }>(`/trade/jobs/${jobId}/start`, { start }),
  tradeFinish: (jobId: number) => post<Ok>(`/trade/jobs/${jobId}/finish`),
  tradeReferrals: () => get<TradeReferrals>('/trade/referrals'),
  setAvailability: (choice: 'on' | 'off' | '1w' | '2w' | '4w') =>
    post<Ok & { status: TradeStatus }>('/trade/availability', { choice }),
};
