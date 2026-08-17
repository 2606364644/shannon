import useSWR from "swr";
import { listHostProfiles } from "./hostProfiles";
import type { HostProfile } from "./types";

/** 稳定空数组：SWR 首帧 data=undefined 时的兜底引用。 */
const EMPTY: HostProfile[] = [];

export interface UseHostProfilesResult {
  profiles: HostProfile[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

/** ws HOST 档案列表（SWR 渐进迁移，2026-08-17）：key ["host-profiles", ws] 与
 *  消费方（HostProfilesPage / ScanFormFields 下拉）共享。无轮询（同 useAuthProfiles）。 */
export function useHostProfiles(workspace: string | undefined): UseHostProfilesResult {
  const { data, error, isLoading, mutate } = useSWR(
    workspace ? ["host-profiles", workspace] : null,
    () => listHostProfiles(workspace!),
  );
  return {
    profiles: data ?? EMPTY,
    loading: isLoading && data === undefined,
    error: error ? (error instanceof Error ? error.message : String(error)) : null,
    refresh: async () => { await mutate(); },
  };
}
