import { useCallback, useRef, useState } from 'react';
import { useFocusEffect } from 'expo-router';

/**
 * Load something from the API each time the screen comes into view, with
 * pull-to-refresh. `pollMs` re-loads while the screen stays open.
 */
export function useLoad<T>(fetcher: () => Promise<T>, pollMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const fetchRef = useRef(fetcher);
  fetchRef.current = fetcher;

  const load = useCallback(async (pull = false) => {
    if (pull) setRefreshing(true);
    try {
      setData(await fetchRef.current());
      setError(null);
    } catch (e: any) {
      setError(e.message || 'Something went wrong.');
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

  return { data, error, refreshing, reload: load, refresh: () => load(true), setData };
}
