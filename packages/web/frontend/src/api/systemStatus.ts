import { useCallback, useEffect, useState } from "react";
import { apiGet, ApiError } from "./client";

export interface TemporalStatus {
  enabled: boolean;
  host: string;
  last_status: "connected" | "error" | "unknown";
  last_error: string | null;
}

export interface SystemStatus {
  ai_provider: string;
  browser_engine: string;
  temporal: TemporalStatus;
  git_available: boolean;
  version: string;
}

export interface UseSystemStatusResult {
  data: SystemStatus | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

export function useSystemStatus(): UseSystemStatusResult {
  const [data, setData] = useState<SystemStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const d = await apiGet<SystemStatus>("/system-status");
      setData(d);
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? `加载失败(${e.status})` : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { data, loading, error, refresh };
}
