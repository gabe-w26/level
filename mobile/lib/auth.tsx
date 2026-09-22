import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { api, AppConfig, clearToken, Counts, getToken, saveToken, setUnauthorizedHandler, User } from './api';
import { unregisterForPush } from './push';

interface AuthContextType {
  user: User | null;
  token: string | null;
  counts: Counts;
  isLoading: boolean;
  /** Categories, areas and options for forms. Loaded once; null until then. */
  config: AppConfig | null;
  loadConfig: () => Promise<AppConfig | null>;
  signIn: (token: string, user: User) => Promise<void>;
  signOut: () => Promise<void>;
  /** Re-read the signed-in person and their badge counts. */
  refresh: () => Promise<void>;
}

const EMPTY: Counts = { notifications: 0, messages: 0, offers: 0 };
const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [counts, setCounts] = useState<Counts>(EMPTY);
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const signingOut = useRef(false);

  const loadConfig = useCallback(async () => {
    try {
      const c = await api.config();
      setConfig(c);
      return c;
    } catch (_) {
      return null;
    }
  }, []);

  const forget = useCallback(async () => {
    await clearToken();
    setToken(null);
    setUser(null);
    setCounts(EMPTY);
  }, []);

  useEffect(() => {
    // A 401 anywhere means the token was revoked (signed out elsewhere, account closed)
    setUnauthorizedHandler(() => { forget(); });
    (async () => {
      loadConfig();
      const stored = await getToken();
      if (stored) {
        setToken(stored);
        try {
          const me = await api.me();
          setUser(me.user);
          setCounts(me.counts);
        } catch (e: any) {
          // Offline: keep the token and try again later. Rejected: sign out.
          if (e?.status === 401 || e?.status === 403) await forget();
        }
      }
      setIsLoading(false);
    })();
    return () => setUnauthorizedHandler(null);
  }, [forget, loadConfig]);

  const refresh = useCallback(async () => {
    if (!(await getToken())) return;
    try {
      const me = await api.me();
      setUser(me.user);
      setCounts(me.counts);
    } catch (_) {}
  }, []);

  async function signIn(newToken: string, newUser: User) {
    await saveToken(newToken);
    setToken(newToken);
    setUser(newUser);
    refresh();
  }

  async function signOut() {
    if (signingOut.current) return;
    signingOut.current = true;
    try {
      const pushToken = await unregisterForPush();
      await api.logout(pushToken).catch(() => {});
    } finally {
      await forget();
      signingOut.current = false;
    }
  }

  return (
    <AuthContext.Provider value={{ user, token, counts, isLoading, config, loadConfig, signIn, signOut, refresh }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}

/** The form options, loading them if the first try failed (e.g. offline at launch). */
export function useAppConfig() {
  const { config, loadConfig } = useAuth();
  useEffect(() => {
    if (!config) loadConfig();
  }, [config, loadConfig]);
  return config;
}
