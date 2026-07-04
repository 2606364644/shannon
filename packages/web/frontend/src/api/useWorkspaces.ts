import { useCallback, useEffect, useRef, useState } from "react";
import { apiGet, ApiError } from "./client";
import type { Workspace } from "./types";

export interface UseWorkspacesResult {
  data: Workspace[];
  loading: boolean;
  lastUpdated: Date | null;
  error: string | null;
  refresh: () => Promise<void>;
}

export function useWorkspaces(intervalMs = 5000): UseWorkspacesResult {
  const [data, setData] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refresh = useCallback(async () => {
    try {
      const rows = await apiGet<Workspace[]>("/workspaces");
      setData(rows);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? `加载失败（${e.status}）` : "加载失败");
    } finally {
      setLastUpdated(new Date());
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    timerRef.current = setInterval(refresh, intervalMs);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [refresh, intervalMs]);

  return { data, loading, lastUpdated, error, refresh };
}
