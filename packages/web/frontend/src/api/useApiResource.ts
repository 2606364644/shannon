import useSWR from "swr";
import { apiGet, apiGetText } from "./client";

/**
 * 按 path 拉取的通用 SWR 资源 hook（2026-08-17 批次 Task 4）。
 * key 即 path 字符串（api client 的 URL 天然唯一）→ 同 path 多消费方自动去重，
 * 切 tab 重挂载时缓存即时显示。ReportTab 报告正文（text）/ DeliverablesTab
 * 产物 summary（json）用。path=null 挂起（ws/scanId 未定时）。
 * 组件内单点消费、无需 mutate 的场景直接用；需 refresh 的话加返回值即可（YAGNI）。
 */

export interface UseApiTextResult {
  text: string;
  loading: boolean;
  error: string | null;
}

export function useApiText(path: string | null): UseApiTextResult {
  const { data, error, isLoading } = useSWR(path, (p: string) => apiGetText(p));
  return {
    text: data ?? "",
    loading: isLoading && data === undefined,
    error: error ? (error instanceof Error ? error.message : String(error)) : null,
  };
}

export interface UseApiJsonResult<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

export function useApiJson<T>(path: string | null): UseApiJsonResult<T> {
  const { data, error, isLoading } = useSWR(path, (p: string) => apiGet<T>(p));
  return {
    data: data ?? null,
    loading: isLoading && data === undefined,
    error: error ? (error instanceof Error ? error.message : String(error)) : null,
  };
}
