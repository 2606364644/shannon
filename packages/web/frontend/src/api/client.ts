import type { FsBrowseResult, Repo, RepoDetail, ScanSummary, SessionData } from "./types";

export class ApiError extends Error {
  constructor(public status: number, public body: unknown) {
    super(`API ${status}`);
    this.name = "ApiError";
  }
}

export type ReqOptions = { silent?: boolean; signal?: AbortSignal };

function defaultUnauthorizedHandler(): void {
  // 已在 /login 时不重复跳转--防 BrandProvider 等全局组件在未登录 /login 页发
  // 非 silent 401 -> assign("/login?expired=1") -> 整页刷新 -> 重新 mount -> 循环
  // （login 页"一直在重复刷新"的根治防御）。
  if (window.location.pathname === "/login") return;
  window.location.assign("/login?expired=1");
}
let onUnauthorized: () => void = defaultUnauthorizedHandler;
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}
export function resetUnauthorizedHandler() {
  onUnauthorized = defaultUnauthorizedHandler;
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
export const apiPut = <T>(path: string, body: unknown, opts?: ReqOptions) =>
  request<T>(path, { method: "PUT", body: JSON.stringify(body) }, opts);
export const apiDelete = <T>(path: string, opts?: ReqOptions) =>
  request<T>(path, { method: "DELETE" }, opts);
export const apiPatch = <T>(path: string, body: unknown, opts?: ReqOptions) =>
  request<T>(path, { method: "PATCH", body: JSON.stringify(body) }, opts);

export const browseFs = (path: string) =>
  apiGet<FsBrowseResult>(`/fs/browse?path=${encodeURIComponent(path)}`);
export const createWorkspace = (name: string) =>
  apiPost<{ name: string }>("/workspaces", { name });
export const deleteWorkspace = (ws: string) =>
  apiDelete<{ deleted: string }>(`/workspaces/${encodeURIComponent(ws)}`);
export type CancelResult = { cancelled: string; via?: string; was_dead?: boolean };
// ws-scan 解耦：cancelScan 走 scan-scoped POST /cancel（动作型 POST，对齐 resume POST；
// DELETE /scans/{id} 已让位给真删除）。ws 列表行只有 ws 名、无 scan_id -> 用 cancelActiveScan
// 先 listScans 解析出在跑的 scan 再取消。
export function cancelScan(ws: string, scanId: string): Promise<CancelResult> {
  return apiPost<CancelResult>(`/workspaces/${encWs(ws)}/scans/${encWs(scanId)}/cancel`, {});
}

/** 取消该 ws 正在跑的 scan（ws 列表行用：无 scan_id，先 listScans 找 active/latest running，再 scan-scoped 取消）。
 *  对齐旧 DELETE /api/scan/{ws} shim 语义（cancel latest/active）。无在跑 scan -> 抛错（对应旧 shim 的 404）。 */
export async function cancelActiveScan(ws: string): Promise<CancelResult> {
  const scans = await listScans(ws);
  const active = scans.find((s) => s.is_running || s.status === "running");
  if (!active) {
    throw new Error("该工作区当前无在跑的扫描");
  }
  return cancelScan(ws, active.scan_id);
}

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

/** 批量关联父目录下所有 git 仓库。admin-only；返回 {imported, skipped}。 */
export const linkReposInDir = (ws: string, body: { path: string }) =>
  apiPost<{ imported: { name: string; path: string }[]; skipped: { name?: string; path: string; reason: string }[] }>(
    `/workspaces/${encWs(ws)}/repos/link-dir`, body);
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

// === ws-scan 解耦（spec §5.1）：scan-scoped API helper ===
// scan_id 是 ws 内单段（YYYYMMDD-HHMMSS[-N]，不含 `/`），用 encWs 单段 encode 即安全。
// Path helper 返不含 /api 前缀的 path（喂 apiGet/apiGetText，内部加 /api）；
// scanEventsUrl 返含 /api 完整 URL（喂 EventSource，对齐 repoEventsUrl 约定）。

/** 列该 ws 的 scans（ScanSummary[]，按 created_at 倒序）。 */
export const listScans = (ws: string) =>
  apiGet<ScanSummary[]>(`/workspaces/${encWs(ws)}/scans`);

/** 跨 ws 聚合所有 scans（IA 重设计 §3，GET /api/scans）。返回项注入 workspace 字段。 */
export const listAllScans = () => apiGet<ScanSummary[]>("/scans");

/** 置顶当前用户的工作区（IA 重设计 §2.3，PUT /api/users/me/pinned-workspace）。 */
export const setPinnedWorkspace = (ws: string) =>
  apiPut<{ pinned: string }>("/users/me/pinned-workspace", { workspace: ws });

/** scan 详情（同旧 GET /workspaces/{ws} payload shape，读 scan_dir）。 */
export const getScan = (ws: string, scanId: string) =>
  apiGet<SessionData>(`/workspaces/${encWs(ws)}/scans/${encWs(scanId)}`);

/** 综合报告 + PoC（text/plain）path--喂 apiGetText。 */
export const scanReportPath = (ws: string, scanId: string) =>
  `/workspaces/${encWs(ws)}/scans/${encWs(scanId)}/report`;

/** 产物摘要（无 file）或单产物文件内容（带 file path）--摘要喂 apiGet，文件喂 apiGetText。 */
export const scanDeliverablesPath = (ws: string, scanId: string, file?: string) =>
  file
    ? `/workspaces/${encWs(ws)}/scans/${encWs(scanId)}/deliverables?path=${encodeURIComponent(file)}`
    : `/workspaces/${encWs(ws)}/scans/${encWs(scanId)}/deliverables`;

/** 日志文件列表（无 file）或单日志内容（带 file）--都喂 apiGet。 */
export const scanLogsPath = (ws: string, scanId: string, file?: string) =>
  file
    ? `/workspaces/${encWs(ws)}/scans/${encWs(scanId)}/logs?file=${encodeURIComponent(file)}`
    : `/workspaces/${encWs(ws)}/scans/${encWs(scanId)}/logs`;

/** scan events SSE URL（tail scan_dir/events.ndjson）--喂 EventSource，含 /api 前缀。 */
export const scanEventsUrl = (ws: string, scanId: string) =>
  `/api/workspaces/${encWs(ws)}/scans/${encWs(scanId)}/events`;

/** 恢复未完成 scan（仅非终态放行，终态后端 422）。返回对齐 ScanAccepted 风格。 */
export type ResumeResult = { workspace: string; scan_id: string };
export const resumeScan = (ws: string, scanId: string) =>
  apiPost<ResumeResult>(`/workspaces/${encWs(ws)}/scans/${encWs(scanId)}/resume`, {});

/** 删除单个 scan（删 scan 不删 ws，spec §5.1 DELETE）。 */
export const deleteScan = (ws: string, scanId: string) =>
  apiDelete<{ deleted: string }>(`/workspaces/${encWs(ws)}/scans/${encWs(scanId)}`);

/** report 端点返 text/plain，deliverables?path= 单文件内容也走文本。不做 JSON.parse。
 *  注：此端点不经统一 request()，故不带 CSRF/401 处理——仅为兼容现有 text 调用。 */
export async function apiGetText(path: string): Promise<string> {
  const res = await fetch(`/api${path}`, { credentials: "include" });
  if (!res.ok) throw new ApiError(res.status, await res.text());
  return res.text();
}
