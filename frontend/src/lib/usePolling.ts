import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Bounded polling hook for live operator views.
 *
 * - runs `fn` immediately and then every `intervalMs` while the tab is visible;
 * - pauses when the document is hidden (resumes on visibility);
 * - prevents overlapping in-flight fetches;
 * - exposes `refresh()` for a manual trigger.
 */
export function usePolling<T>(
  fn: () => Promise<T>,
  intervalMs: number,
  deps: unknown[] = [],
): { data: T | null; error: string | null; loading: boolean; refresh: () => void } {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshToken, setRefreshToken] = useState(0);
  const inFlight = useRef(false);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    let cancelled = false;

    const run = async () => {
      if (inFlight.current || cancelled) return;
      inFlight.current = true;
      try {
        const result = await fnRef.current();
        if (!cancelled) {
          setData(result);
          setError(null);
          setLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
        }
      } finally {
        inFlight.current = false;
      }
    };

    void run();

    const id = window.setInterval(() => {
      if (document.hidden) return;
      void run();
    }, intervalMs);

    const onVisibility = () => {
      if (!document.hidden) void run();
    };
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      cancelled = true;
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisibility);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, refreshToken]);

  const refresh = useCallback(() => setRefreshToken((t) => t + 1), []);

  return { data, error, loading, refresh };
}
