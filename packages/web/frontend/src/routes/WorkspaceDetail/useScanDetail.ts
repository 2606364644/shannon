import useSWR from "swr";
import { getScan } from "@/api/client";
import type { SessionData } from "@/api/types";

export interface UseScanDetailResult {
  data: SessionData | null;
  loading: boolean;
  error: string | null;
  /** silent revalidate：不翻 loading、不卸载已有内容（保 ScanProgressOverview 的
   *  scan_end 一次性通知 ref），取代旧 load(silent) 手写双分支。 */
  refresh: () => Promise<void>;
}

/** scan 详情（SWR 迁移，2026-08-17 批次 Task 2）：key ["scan", ws, scanId] 由
 *  ScanDetail / OverviewTab / ReportTab(combined 探测) 共享 → 单请求取代此前
 *  三方各拉各一份；切 tab 重挂载时缓存即时显示 + 后台 revalidate。
 *  refresh = SWR mutate（silent 语义天然成立：data 保留、不闪 Skeleton）。 */
export function useScanDetail(
  workspace: string | undefined, scanId: string | undefined,
): UseScanDetailResult {
  const { data, error, isLoading, mutate } = useSWR(
    workspace && scanId ? ["scan", workspace, scanId] : null,
    () => getScan(workspace!, scanId!),
  );
  return {
    data: data ?? null,
    loading: isLoading && data === undefined,
    error: error ? (error instanceof Error ? error.message : String(error)) : null,
    refresh: async () => { await mutate(); },
  };
}
