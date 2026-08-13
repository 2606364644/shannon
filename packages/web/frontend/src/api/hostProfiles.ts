// HOST 档案库 client（blackbox-host-profile, Task 10 契约）。
// Pattern B：mirror authProfiles.ts —— 顶部 import helper + const enc = encodeURIComponent；
// 每个端点一个 arrow const，路径参数经 enc 单段 encode。
//
// 后端契约（plan blackbox-host-profile）：
//   GET    /workspaces/{ws}/host-profiles                    -> HostProfile[]
//   GET    /workspaces/{ws}/host-profiles/{pid}              -> HostProfile
//   POST   /workspaces/{ws}/host-profiles                    -> HostProfile   (body: {name, source_url?, mappings})
//   POST   /workspaces/{ws}/host-profiles/parse?url=<URL>    -> {mappings, warnings}   (不落盘，预览)
//   PUT    /workspaces/{ws}/host-profiles/{pid}              -> { ok: true }
//   DELETE /workspaces/{ws}/host-profiles/{pid}              -> { ok: true }
//   POST   /workspaces/{ws}/host-profiles/{pid}/fork         -> HostProfile   (系统档案复制为可编辑 ws 副本)
//   POST   /workspaces/{ws}/host-profiles/{pid}/refresh      -> HostProfile   (按 source_url 重新拉取)
//
// 注：apiPost 的 ReqOptions = { silent?, signal? }，无 params。parse 的 ?url= 查询串手动拼
// （对齐 authProfiles.ts getVerifyStatus 范式：path + "?k=" + enc(v)）。
import { apiGet, apiPost, apiPut, apiDelete } from "./client";
import type { HostProfile } from "./types";

const enc = encodeURIComponent;

export const listHostProfiles = (ws: string) =>
  apiGet<HostProfile[]>(`/workspaces/${enc(ws)}/host-profiles`);
export const getHostProfile = (ws: string, pid: string) =>
  apiGet<HostProfile>(`/workspaces/${enc(ws)}/host-profiles/${enc(pid)}`);
export const createHostProfile = (ws: string, body: Partial<HostProfile>) =>
  apiPost<HostProfile>(`/workspaces/${enc(ws)}/host-profiles`, body);
export const updateHostProfile = (ws: string, pid: string, body: Partial<HostProfile>) =>
  apiPut<{ ok: true }>(`/workspaces/${enc(ws)}/host-profiles/${enc(pid)}`, body);
export const deleteHostProfile = (ws: string, pid: string) =>
  apiDelete<{ ok: true }>(`/workspaces/${enc(ws)}/host-profiles/${enc(pid)}`);
export const forkHostProfile = (ws: string, pid: string) =>
  apiPost<HostProfile>(`/workspaces/${enc(ws)}/host-profiles/${enc(pid)}/fork`, {});
export const refreshHostProfile = (ws: string, pid: string) =>
  apiPost<HostProfile>(`/workspaces/${enc(ws)}/host-profiles/${enc(pid)}/refresh`, {});

/** 预解析（不落盘）：从 /etc/hosts 风格 URL 拉取并解析 mappings 供预览填表。
 *  url 走 query string（后端 POST /parse?url=<URL>，body 空 {}）。 */
export const parseHostProfile = (ws: string, url: string) =>
  apiPost<{ mappings: { ip: string; host: string }[]; warnings: string[] }>(
    `/workspaces/${enc(ws)}/host-profiles/parse?url=${enc(url)}`, {});
