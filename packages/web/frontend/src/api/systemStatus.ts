import { useCallback, useEffect, useState } from "react";
import { apiGet, ApiError } from "./client";
import i18n from "@/i18n";

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
  git: { binary_available: boolean; credentials_configured: boolean };
  version: string;
  brand_name: string;
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
      setError(
        e instanceof ApiError
          ? i18n.t("common.loadFailedStatus", { status: e.status })
          : i18n.t("common.loadFailed"),
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { data, loading, error, refresh };
}
