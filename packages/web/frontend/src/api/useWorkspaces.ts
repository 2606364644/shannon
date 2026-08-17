import { useCallback } from "react";
import useSWR from "swr";
import { apiGet, ApiError } from "./client";
import i18n from "@/i18n";
import type { Workspace } from "./types";

/** 稳定空数组：SWR 首帧 data=undefined 时的兜底引用。 */
const EMPTY: Workspace[] = [];

export interface UseWorkspacesResult {
  data: Workspace[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

/** SWR 版（spec §D）：refreshInterval 轮询 + SWR 默认 refreshWhenHidden=false
 *  （后台 tab 自动停轮询）+ revalidateOnFocus（回前台刷新）——取代手写 setInterval。
 *  lastUpdated 无消费者，已删。 */
export function useWorkspaces(intervalMs = 5000): UseWorkspacesResult {
  const { data, isLoading, error, mutate } = useSWR<Workspace[]>(
    "/workspaces",
    (path: string) => apiGet<Workspace[]>(path),
    { refreshInterval: intervalMs },
  );
  const refresh = useCallback(async () => { await mutate(); }, [mutate]);
  return {
    data: data ?? EMPTY,
    loading: isLoading && data === undefined,
    error: error
      ? error instanceof ApiError
        ? i18n.t("common.loadFailedStatus", { status: error.status })
        : i18n.t("common.loadFailed")
      : null,
    refresh,
  };
}
