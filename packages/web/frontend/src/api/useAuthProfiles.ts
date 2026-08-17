import useSWR from "swr";
import { listAuthProfiles } from "./authProfiles";
import type { AuthProfile } from "./types";

/** 稳定空数组：SWR 首帧 data=undefined 时的兜底引用。 */
const EMPTY: AuthProfile[] = [];

export interface UseAuthProfilesResult {
  profiles: AuthProfile[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

/** ws 认证档案列表（SWR 渐进迁移，2026-08-17）：key ["auth-profiles", ws] 与
 *  消费方（AuthProfilesPage / ScanFormFields 下拉）共享 → 单请求 + 缓存即时显示，
 *  取代旧 useState+refresh 每次进 tab 全屏 Skeleton。无轮询（档案非运行态数据，
 *  revalidateOnFocus 默认开即可）。 */
export function useAuthProfiles(workspace: string | undefined): UseAuthProfilesResult {
  const { data, error, isLoading, mutate } = useSWR(
    workspace ? ["auth-profiles", workspace] : null,
    () => listAuthProfiles(workspace!),
  );
  return {
    profiles: data ?? EMPTY,
    loading: isLoading && data === undefined,
    error: error ? (error instanceof Error ? error.message : String(error)) : null,
    refresh: async () => { await mutate(); },
  };
}
