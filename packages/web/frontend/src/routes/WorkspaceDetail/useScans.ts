import useSWR from "swr";
import { listScans, ApiError } from "@/api/client";
import type { ScanSummary } from "@/api/types";

const isRunning = (s: ScanSummary) => s.is_running || s.status === "running";

/** 稳定空数组：SWR 首帧 data=undefined 时的兜底引用。 */
const EMPTY_SCANS: ScanSummary[] = [];

export interface UseScansResult {
  scans: ScanSummary[];
  loading: boolean;
  notFound: boolean;
  error: string | null;
  refresh: () => void;
}

/** ws 扫描列表（spec §6.3）：WorkspaceDetail 容器与 ScanList 共用同一 key
 *  （["scans", workspace]）→ SWR 去重为单请求 + 单份 10s 条件轮询（运行中才轮询，
 *  后台 tab 暂停），取代此前父子各拉各轮询的双份流量。 */
export function useScans(workspace: string | undefined): UseScansResult {
  const { data, error, isLoading, mutate } = useSWR(
    workspace ? ["scans", workspace] : null,
    () => listScans(workspace!),
    { refreshInterval: (latest?: ScanSummary[]) => (latest?.some(isRunning) ? 10_000 : 0) },
  );
  return {
    scans: data ?? EMPTY_SCANS,
    loading: isLoading && data === undefined,
    notFound: error instanceof ApiError && error.status === 404,
    error: error ? (error instanceof Error ? error.message : String(error)) : null,
    refresh: () => { void mutate(); },
  };
}
