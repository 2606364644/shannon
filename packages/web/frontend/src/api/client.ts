import type { FsBrowseResult } from "./types";

export class ApiError extends Error {
  constructor(public status: number, public body: unknown) { super(`API ${status}`); this.name = "ApiError"; }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let body: unknown;
    try { body = await res.json(); } catch { body = await res.text(); }
    throw new ApiError(res.status, body);
  }
  // 204/无 body
  const text = await res.text();
  return (text ? JSON.parse(text) : {}) as T;
}

export const apiGet = <T>(path: string) => request<T>(path);
export const apiPost = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });
export const apiDelete = <T>(path: string) => request<T>(path, { method: "DELETE" });

export const browseFs = (path: string) =>
  apiGet<FsBrowseResult>(`/fs/browse?path=${encodeURIComponent(path)}`);
export const deleteWorkspace = (ws: string) =>
  apiDelete<{ deleted: string }>(`/workspaces/${encodeURIComponent(ws)}`);
export const cancelScan = (ws: string) =>
  apiDelete<{ cancelled: string }>(`/scan/${encodeURIComponent(ws)}`);

/** report 端点返 text/plain，deliverables?path= 单文件内容也走文本。不做 JSON.parse。 */
export async function apiGetText(path: string): Promise<string> {
  const res = await fetch(`/api${path}`);
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return res.text();
}
