import useSWR from "swr";
import { listRepos } from "./client";
import type { Repo } from "./types";

/** 稳定空数组：SWR 首帧 data=undefined 时的兜底引用。 */
const EMPTY: Repo[] = [];

export interface UseReposResult {
  repos: Repo[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

/** ws 仓库列表（SWR 迁移，2026-08-17 批次 Task 3）：key ["repos", ws] 由 ReposTab
 *  与 ScanFormFields 下拉共享 → 单请求 + 缓存即时显示。无轮询（clone/pull 完成由
 *  调用方按事件 refresh——与旧 refresh() 调用点一致）。
 *  enabled=false（auth user 未就绪）时 key null 挂起：等 user ready 再发首请求
 *  （对齐旧 useEffect 对 user 的守卫语义）。 */
export function useRepos(workspace: string | undefined, enabled = true): UseReposResult {
  const { data, error, isLoading, mutate } = useSWR(
    workspace && enabled ? ["repos", workspace] : null,
    () => listRepos(workspace!),
  );
  return {
    repos: data ?? EMPTY,
    loading: isLoading && data === undefined,
    error: error ? (error instanceof Error ? error.message : String(error)) : null,
    refresh: async () => { await mutate(); },
  };
}
