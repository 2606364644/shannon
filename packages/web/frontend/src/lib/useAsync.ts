import { useCallback, useEffect, useState } from "react";

/**
 * 通用异步数据 hook -- 触发一次 fn,把结果/加载/错误状态化暴露。
 *
 * 设计取舍(对齐 DashboardPage 跨 ws 扫描聚合的消费场景):
 * - `data` 初值用 `[] as unknown as T` -- 调用方(DashboardPage)按数组语义消费
 *   (`data.length` / `data.filter` / `useScanFilters(data, ...)`),初值空数组避免
 *   `data is possibly undefined` 的 null 守卫噪音;非数组 T 由调用方自行兜底。
 * - `refresh` 经 `useCallback(deps)` 稳定化,`useEffect([refresh])` 仅 mount 触发一次;
 *   deps 透传调用方依赖(如 listAllScans 这类 import 稳定的传 [] 即可)。
 * - 不做 auto-refetch / 轮询 -- 保持单次语义,轮询由消费方(如 live 页)自管。
 */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T>([] as unknown as T);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    try {
      const r = await fn();
      setData(r);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => {
    refresh();
  }, [refresh]);
  return { data, loading, error, refresh };
}
