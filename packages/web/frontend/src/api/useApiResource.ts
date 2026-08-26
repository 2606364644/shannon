import useSWR from "swr";
import { ApiError, apiGet, apiGetText } from "./client";
import type { ReportData } from "./types";

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

export interface UseReportDataResult {
  data: ReportData | null;
  loading: boolean;
  error: string | null;
  /** 404（旧 scan 无 report_data.json）→ 调用方回退 md 渲染路径（降级分支）。 */
  notFound: boolean;
}

/**
 * report_data.json（spec 2026-08-26 §7.1，T6）：结构化报告 SSOT 的 SWR 拉取。
 * key 即 path（与 useApiText 同缓存策略）。notFound 单列——404 是「旧 scan 走 md
 * 降级」的正常分流信号，非错误态；其余错误（网络/5xx）交调用方显 ErrorState。
 */
export function useReportData(path: string | null): UseReportDataResult {
  const { data, error, isLoading } = useSWR(path, (p: string) => apiGet<ReportData>(p));
  return {
    data: data ?? null,
    loading: isLoading && data === undefined,
    error: error ? (error instanceof Error ? error.message : String(error)) : null,
    notFound: error instanceof ApiError && error.status === 404,
  };
}
