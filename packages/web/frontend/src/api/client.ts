import type { FsBrowseResult, Repo, RepoDetail } from "./types";

export class ApiError extends Error {
  constructor(public status: number, public body: unknown) {
    super(`API ${status}`);
    this.name = "ApiError";
  }
}

export type ReqOptions = { silent?: boolean; signal?: AbortSignal };

let onUnauthorized: () => void = () => window.location.assign("/login?expired=1");
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

function readCookie(name: string): string | null {
  const m = document.cookie.match(
    new RegExp("(?:^|; )" + name.replace(/([.$?*|{}()[\]\\/+^])/g, "\\$1") + "=([^;]*)"),
  );
  return m ? decodeURIComponent(m[1]) : null;
}

const WRITE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

async function request<T>(path: string, init?: RequestInit, opts?: ReqOptions): Promise<T> {
  const method = init?.method?.toUpperCase();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  if (method && WRITE_METHODS.has(method)) {
    const tok = readCookie("sn-csrf");
    if (tok) headers["X-CSRF-Token"] = tok;
  }
  const res = await fetch(`/api${path}`, {
    ...init,
    headers,
    credentials: "include",
    signal: opts?.signal,
  });
  if (!res.ok) {
    if (res.status === 401 && !opts?.silent) onUnauthorized();
    let body: unknown;
    try {
      body = await res.json();
    } catch {
      body = await res.text();
    }
    throw new ApiError(res.status, body);
  }
  // 204/无 body
  const text = await res.text();
  return (text ? JSON.parse(text) : {}) as T;
}

export const apiGet = <T>(path: string, opts?: ReqOptions) => request<T>(path, undefined, opts);
export const apiPost = <T>(path: string, body: unknown, opts?: ReqOptions) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) }, opts);
export const apiDelete = <T>(path: string, opts?: ReqOptions) =>
  request<T>(path, { method: "DELETE" }, opts);

export const browseFs = (path: string) =>
  apiGet<FsBrowseResult>(`/fs/browse?path=${encodeURIComponent(path)}`);
export const createWorkspace = (name: string) =>
  apiPost<{ name: string }>("/workspaces", { name });
export const deleteWorkspace = (ws: string) =>
  apiDelete<{ deleted: string }>(`/workspaces/${encodeURIComponent(ws)}`);
export type CancelResult = { cancelled: string; via?: string; was_dead?: boolean };
export const cancelScan = (ws: string) =>
  apiDelete<CancelResult>(`/scan/${encodeURIComponent(ws)}`);

/** 仓库名（可为 group/repo）按段 encode：保留 `/` 作路径分隔，每段安全转义。
 *  /workspaces/<ws>/repos/frontend/foo 直接命中后端 {name:path}，含空格等特殊字符的段也安全。 */
const encRepo = (name: string) => name.split("/").map(encodeURIComponent).join("/");

/** workspace 名整体 encode（不允许 `/`，作为单段 path 参数）。 */
const encWs = (ws: string) => encodeURIComponent(ws);

// P2: 仓库已迁到 ws 内——所有 repo API 路径前置 /workspaces/<ws>，对齐后端
//     POST/GET/DELETE /api/workspaces/{ws}/repos[/{name:path}] (+ /pull, /checkout, /events)
export const listRepos = (ws: string) =>
  apiGet<Repo[]>(`/workspaces/${encWs(ws)}/repos`);
export const getRepo = (ws: string, name: string) =>
  apiGet<RepoDetail>(`/workspaces/${encWs(ws)}/repos/${encRepo(name)}`);
export const createRepo = (
  ws: string,
  body: { git_url: string; branch?: string; commit?: string; name?: string; group?: string },
) => apiPost<{ name: string }>(`/workspaces/${encWs(ws)}/repos`, body);
export const deleteRepo = (ws: string, name: string) =>
  apiDelete<{ deleted: string }>(`/workspaces/${encWs(ws)}/repos/${encRepo(name)}`);
export const pullRepo = (ws: string, name: string) =>
  apiPost<{ pulling: string }>(`/workspaces/${encWs(ws)}/repos/${encRepo(name)}/pull`, {});
export const checkoutRepo = (ws: string, name: string, branch: string) =>
  apiPost<{ checked_out: string }>(`/workspaces/${encWs(ws)}/repos/${encRepo(name)}/checkout`, { branch });

/** CloneProgress SSE 路径——client 这层不直接消费（CloneProgress 自管 useEventSource），
 *  暴露给组件层用，避免各处再写一遍 ws+name 拼接。 */
export const repoEventsUrl = (ws: string, name: string) =>
  `/api/workspaces/${encWs(ws)}/repos/${encRepo(name)}/events`;

/** report 端点返 text/plain，deliverables?path= 单文件内容也走文本。不做 JSON.parse。
 *  注：此端点不经统一 request()，故不带 CSRF/401 处理——仅为兼容现有 text 调用。 */
export async function apiGetText(path: string): Promise<string> {
  const res = await fetch(`/api${path}`, { credentials: "include" });
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return res.text();
}
