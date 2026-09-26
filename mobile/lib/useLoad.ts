import { useCallback, useRef, useState } from 'react';
import { useFocusEffect } from 'expo-router';

/**
 * Load something from the API each time the screen comes into view, with
 * pull-to-refresh. `pollMs` re-loads while the screen stays open.
 */
export function useLoad<T>(fetcher: () => Promise<T>, pollMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const fetchRef = useRef(fetcher);
  fetchRef.current = fetcher;
  // What we last managed to load, read inside the catch — state would be a
  // stale closure there, and this decides which of the two failures it is.
  const have = useRef(false);

  const load = useCallback(async (pull = false) => {
    if (pull) setRefreshing(true);
    try {
      setData(await fetchRef.current());
      have.current = true;
      setError(null);
      setStale(false);
    } catch (e: any) {
      // Two different failures that used to look identical. A screen with
      // nothing on it needs the red — there's nothing to read and something to
      // do about it. A background poll that drops out while a conversation is
      // sitting there readable does not: what's on screen is still true, just
      // not up to the minute, and a wall of red over it reads as "your message
      // failed" when nothing of the sort happened.
      if (have.current) setStale(true);
      else setError(e.message || 'Something went wrong.');
    } finally {
      setRefreshing(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
      if (!pollMs) return;
      const t = setInterval(() => load(), pollMs);
      return () => clearInterval(t);
    }, [load, pollMs])
  );

  return { data, error, stale, refreshing, reload: load, refresh: () => load(true), setData };
}
